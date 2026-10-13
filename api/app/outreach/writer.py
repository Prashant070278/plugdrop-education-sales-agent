"""Draft personalised outreach emails and check them against the messaging policy.

Inputs are limited to:
* approved research findings already pushed into the CRM (verified facts and source-supported inferences),
* approved items from the content library (value propositions, claims, case studies, calls to action),
* approved templates and the admin messaging policy.

Two writers exist: ``ClaudeWriter`` (Claude API, structured output) and ``RuleWriter`` (deterministic).
Whatever writes the draft, ``quality_check`` decides whether it can be approved; it is re-run after
every human edit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings

HEDGES = ("looks like", "seems", "appears", "may ", "might", "if i've", "correct me", "i understand", "it sounds")
FALSE_RELATIONSHIP = (
    "as we discussed",
    "as promised",
    "following up on our conversation",
    "great speaking",
    "great to meet",
    "per our call",
    "we spoke",
    "when we met",
    "you requested",
    "our partnership",
    "as you know",
)
CATEGORY_PHRASES = {
    "enquiry_volume": (
        "your admissions team handles a large volume of enquiries each intake",
        "your admissions team may be handling a high volume of enquiries",
    ),
    "hiring_admissions": ("you're hiring admissions counsellors", "you may be adding admissions counsellors"),
    "hiring_contact_centre": ("you're recruiting for your contact centre", "your contact centre may be growing"),
    "new_campus": ("you're opening a new campus", "a new campus may be on the way"),
    "new_course": ("you're launching a new programme", "a new programme may be launching"),
    "intake_period": ("applications for your next intake close soon", "your next intake deadline may be approaching"),
    "admissions_campaign": ("your admissions campaign is under way", "an admissions campaign may be under way"),
    "hiring_crm": ("you're recruiting for a CRM role", "a CRM role may be open"),
    "crm_initiative": ("a CRM change is under way", "a CRM change may be under way"),
    "telephony_initiative": (
        "you're reviewing contact-centre telephony and CRM",
        "contact-centre telephony and CRM may be under review",
    ),
    "digital_transformation": (
        "you've announced a digital transformation programme",
        "a digital transformation programme may be under way",
    ),
    "response_delays": (
        "applicants have mentioned waiting for replies during peak season",
        "some applicants may be waiting a while for replies at peak times",
    ),
    "multi_campus": ("you run admissions across several campuses", "admissions may run across several campuses"),
}
USE_CASE_SUBJECT = {
    "voice_enquiry_automation": "Admissions enquiries at {inst}",
    "enquiry_qualification": "Qualifying enquiries at {inst}",
    "counselling": "Counselling capacity at {inst}",
    "missed_call_recovery": "Missed admissions calls at {inst}",
    "follow_up": "Applicant follow-up at {inst}",
    "whatsapp_engagement": "WhatsApp enquiries at {inst}",
    "crm_integration": "Enquiries and your CRM at {inst}",
}


class WriterError(Exception):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


@dataclass
class Fact:
    id: str  # "F<research_finding_id>"
    category: str
    level: str  # verified_fact | source_inference
    text: str
    excerpt: str | None
    source_url: str | None
    source_title: str | None
    published_at: str | None


@dataclass
class Content:
    id: str  # "C<content_item_id>"
    kind: str
    title: str
    body: str
    use_cases: list[str]


@dataclass
class Context:
    institution: str
    first_name: str
    contact_name: str
    designation: str | None
    sender_name: str
    use_case: str | None
    facts: list[Fact]
    content: list[Content]
    policy: dict[str, Any]
    step: int = 1
    kind: str = "sequence"  # sequence | reply
    previous: list[dict[str, str]] = field(default_factory=list)  # earlier emails in this sequence
    reply: dict[str, str] | None = None  # the prospect's reply, for reply drafts
    template: dict[str, str] | None = None


@dataclass
class Draft:
    subject: str
    body: str  # without signature / opt-out (added by ``finalize``)
    facts_used: list[str]
    content_used: list[str]
    rationale: str
    generated_by: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def signature(ctx: Context) -> str:
    return ctx.policy["signature"].replace("{sender_name}", ctx.sender_name)


def finalize(ctx: Context, body: str) -> str:
    """Append the signature and the opt-out line (always present on every email)."""
    return f"{body.strip()}\n\n{signature(ctx)}\n\n{ctx.policy['opt_out_line']}"


def strip_footer(ctx: Context, full: str) -> str:
    out = full.replace(ctx.policy["opt_out_line"], "")
    return out.replace(signature(ctx), "").strip()


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.") if url else ""


def _pick(content: list[Content], kind: str, use_case: str | None) -> Content | None:
    exact = [c for c in content if c.kind == kind and use_case and use_case in c.use_cases]
    general = [c for c in content if c.kind == kind and not c.use_cases]
    found = exact or general
    return found[0] if found else None


def _observation(ctx: Context, f: Fact) -> str:
    verified, inferred = CATEGORY_PHRASES.get(f.category, (f.text[0].lower() + f.text[1:], f.text[0].lower() + f.text[1:]))
    if f.level == "verified_fact":
        src = f" ({_host(f.source_url)})" if f.source_url else ""
        return f"I saw that {verified}{src}."
    return f"From what I've read, it looks like {inferred} — please correct me if I've got that wrong."


# ---------------------------------------------------------------------------
# deterministic writer
# ---------------------------------------------------------------------------
class RuleWriter:
    name = "rules"

    def write(self, ctx: Context) -> Draft:
        if ctx.kind == "reply":
            return self._reply(ctx)
        facts = sorted(ctx.facts, key=lambda f: (f.level != "verified_fact", f.category not in CATEGORY_PHRASES))
        if not facts:
            raise WriterError("No approved research facts for this institution. Approve and push research findings first.")
        vp = _pick(ctx.content, "value_prop", ctx.use_case)
        cta = _pick(ctx.content, "cta", ctx.use_case)
        if vp is None or cta is None:
            missing = [n for n, c in (("value proposition", vp), ("call to action", cta)) if c is None]
            raise WriterError(
                f"The content library has no approved {' or '.join(missing)} for this use case. Ask an admin to add and approve one."
            )
        inst = ctx.institution
        if ctx.step == 1:
            fact = facts[0]
            subject = (ctx.template or {}).get("subject") or USE_CASE_SUBJECT.get(ctx.use_case or "", "Admissions at {inst}")
            body = f"Dear {ctx.first_name},\n\n{_observation(ctx, fact)}\n\n{vp.body}\n\n{cta.body}"
            used_f, used_c = [fact.id], [vp.id, cta.id]
            why = f"Step 1 leads with the strongest fact ({fact.category}, {fact.level}) and the approved value proposition for {ctx.use_case}."
        else:
            # Follow-ups use a different fact where possible and stay shorter.
            fact = facts[min(ctx.step - 1, len(facts) - 1)]
            prev_subject = (
                ctx.previous[0]["subject"] if ctx.previous else USE_CASE_SUBJECT.get(ctx.use_case or "", "Admissions at {inst}")
            )
            subject = prev_subject if prev_subject.lower().startswith("re:") else f"Re: {prev_subject}"
            alt_cta = next(
                (
                    c
                    for c in ctx.content
                    if c.kind == "cta" and c.id != cta.id and (not c.use_cases or ctx.use_case in c.use_cases)
                ),
                cta,
            )
            case = _pick(ctx.content, "case_study", ctx.use_case)
            middle = case.body if (case and ctx.step == 2) else _observation(ctx, fact)
            if ctx.step >= ctx.policy.get("max_attempts", 3):
                body = (
                    f"Dear {ctx.first_name},\n\nI don't want to crowd your inbox, so this is my last note on this. "
                    f"{middle}\n\n{alt_cta.body}"
                )
            else:
                body = f"Dear {ctx.first_name},\n\nFollowing up on my note about enquiries at {inst}. {middle}\n\n{alt_cta.body}"
            used_f = [fact.id] if middle != (case.body if case else None) else []
            used_c = [alt_cta.id] + ([case.id] if case and middle == case.body else [])
            why = f"Follow-up {ctx.step} adds {'an approved case study' if used_c[1:] else 'a different fact'} and a softer call to action."
        return Draft(
            subject=subject.replace("{inst}", inst)[:120],
            body=body.replace("{inst}", inst),
            facts_used=used_f,
            content_used=used_c,
            rationale=why,
            generated_by="rules-v1",
        )

    def _reply(self, ctx: Context) -> Draft:
        label = (ctx.reply or {}).get("label", "unclear")
        first = ctx.first_name
        subj = (ctx.reply or {}).get("subject") or (ctx.previous[0]["subject"] if ctx.previous else "Your reply")
        subject = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        vp = _pick(ctx.content, "value_prop", ctx.use_case)
        templates = {
            "meeting_request": "Thanks — I'd be glad to. Would [EDIT: time option 1] or [EDIT: time option 2] work for a short call?",
            "interested": "Thanks. Would a 20-minute call be useful to look at how this could work for your admissions team? If so, [EDIT: suggest two times].",
            "needs_information": f"Thanks for asking. {vp.body if vp else '[EDIT: add approved information]'}\n\nWould it help if I [EDIT: share an approved overview / answer specific questions]?",
            "not_now": "Thanks — understood. Would it be alright if I checked back in [EDIT: month]?",
            "referral": "Thank you. I'll contact [EDIT: colleague's name] directly and mention that you suggested it — is that alright?",
            "unclear": "Thanks for your reply. [EDIT: respond to their message] Is there anything specific you'd like to know?",
        }
        if label not in templates:
            raise WriterError(f"No reply draft is suggested for '{label}' replies.")
        return Draft(
            subject=subject[:120],
            body=f"Dear {first},\n\n{templates[label]}",
            facts_used=[],
            content_used=[vp.id] if (vp and label == "needs_information") else [],
            rationale=f"Response to a '{label}' reply. Fill the [EDIT] parts before approving.",
            generated_by="rules-v1",
        )


# ---------------------------------------------------------------------------
# Claude writer
# ---------------------------------------------------------------------------
SYSTEM = """You write short B2B sales emails for PlugDrop.AI, which sells Voice AI + CRM to universities, colleges and EdTech \
businesses (admissions enquiries, lead qualification, counselling, missed-call recovery, follow-ups, WhatsApp engagement, CRM integration).

Hard rules:
- Use ONLY the facts and approved content provided. Cite the ids you used in facts_used and content_used.
- A fact with level "source_inference" must be phrased as a qualified observation ("it looks like…", "if I've understood correctly…").
- Explain ONE business problem and ONE possible outcome. Product capabilities, outcomes, customer names and case studies may come ONLY from approved content.
- No numbers, percentages, ROI or savings claims unless they appear in a provided fact or approved content.
- Never imply an existing relationship or previous conversation. No hype words.
- End with one clear, low-friction question as the call to action.
- Do NOT write a signature or an unsubscribe line; they are added automatically.
- Plain text, professional, specific to the institution, within the word limit."""

TOOL = {
    "name": "write_email",
    "description": "Return the email draft.",
    "input_schema": {
        "type": "object",
        "required": ["subject", "body", "facts_used", "content_used", "rationale"],
        "properties": {
            "subject": {"type": "string", "maxLength": 100},
            "body": {"type": "string"},
            "facts_used": {"type": "array", "items": {"type": "string"}},
            "content_used": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
    },
}


class ClaudeWriter:
    name = "claude"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.s = settings
        self.client = client or httpx.Client(base_url=settings.anthropic_base_url, timeout=settings.anthropic_timeout_seconds)

    def write(self, ctx: Context) -> Draft:
        limit = ctx.policy["max_words_first"] if ctx.step == 1 and ctx.kind == "sequence" else ctx.policy["max_words_follow_up"]
        payload = {
            "task": "reply_to_prospect" if ctx.kind == "reply" else ("first_email" if ctx.step == 1 else f"follow_up_{ctx.step}"),
            "word_limit": limit,
            "institution": ctx.institution,
            "recipient": {"first_name": ctx.first_name, "designation": ctx.designation},
            "use_case": ctx.use_case,
            "facts": [f.__dict__ for f in ctx.facts],
            "approved_content": [c.__dict__ for c in ctx.content],
            "banned_phrases": ctx.policy["banned_phrases"],
            "previous_emails": ctx.previous,
            "prospect_reply": ctx.reply,
            "template_hint": ctx.template,
        }
        body = {
            "model": self.s.anthropic_model,
            "max_tokens": 1500,
            "system": SYSTEM,
            "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "write_email"},
            "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        }
        try:
            r = self.client.post(
                "/v1/messages",
                json=body,
                headers={"x-api-key": self.s.anthropic_api_key or "", "anthropic-version": "2023-06-01"},
            )
        except httpx.HTTPError as e:
            raise WriterError(f"Claude API network error: {e.__class__.__name__}", transient=True) from e
        if r.status_code in (429, 500, 502, 503, 504, 529):
            raise WriterError(f"Claude API temporarily unavailable ({r.status_code})", transient=True)
        if r.status_code >= 400:
            raise WriterError(f"Claude API error {r.status_code}")
        data = r.json()
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if not block:
            raise WriterError("Claude returned no draft", transient=True)
        out = block["input"]
        fact_ids, content_ids = {f.id for f in ctx.facts}, {c.id for c in ctx.content}
        usage = data.get("usage", {})
        tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        return Draft(
            subject=str(out.get("subject", ""))[:120],
            body=str(out.get("body", "")).strip(),
            facts_used=[x for x in out.get("facts_used", []) if x in fact_ids],
            content_used=[x for x in out.get("content_used", []) if x in content_ids],
            rationale=str(out.get("rationale", ""))[:500],
            generated_by=f"claude:{self.s.anthropic_model}",
            input_tokens=tin,
            output_tokens=tout,
            cost_usd=tin / 1e6 * self.s.anthropic_input_usd_per_mtok + tout / 1e6 * self.s.anthropic_output_usd_per_mtok,
        )


def get_writer(settings: Settings) -> RuleWriter | ClaudeWriter:
    choice = settings.outreach_writer
    if choice == "auto":
        choice = "claude" if settings.anthropic_api_key else "rules"
    return ClaudeWriter(settings) if choice == "claude" else RuleWriter()


# ---------------------------------------------------------------------------
# quality gate
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r"\b\d[\d,.]*%?|\b\d+x\b", re.I)
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
TIME_DATE_RE = re.compile(
    rf"\b\d{{1,2}}(?::\d{{2}})?\s?(?:am|pm)\b|\b\d{{1,2}}:\d{{2}}\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\b"
    rf"|\b(?:{MONTHS})[a-z]*\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    re.I,
)


def quality_check(ctx: Context, subject: str, full_body: str, facts_used: list[str], content_used: list[str]) -> dict[str, Any]:
    """Return {"passed": bool, "checks": [...]}. Blocking failures prevent approval."""
    body = strip_footer(ctx, full_body)
    text = f"{subject}\n{body}"
    low = text.lower()
    sources = " ".join(
        [f.text + " " + (f.excerpt or "") for f in ctx.facts]
        + [c.body + " " + c.title for c in ctx.content]
        + [ctx.institution, ctx.contact_name]
    )
    used_content = [c for c in ctx.content if c.id in content_used]
    used_facts = [f for f in ctx.facts if f.id in facts_used]
    checks: list[dict[str, Any]] = []

    def add(cid: str, label: str, ok: bool, detail: str = "", blocking: bool = True) -> None:
        checks.append({"id": cid, "label": label, "passed": ok, "blocking": blocking, "detail": "" if ok else detail})

    words = len(re.findall(r"\b\w[\w'’-]*\b", body))
    limit = ctx.policy["max_words_first"] if (ctx.step == 1 and ctx.kind == "sequence") else ctx.policy["max_words_follow_up"]
    add("length", f"Short (≤ {limit} words)", words <= limit, f"{words} words")
    if ctx.kind == "sequence":
        add(
            "personalised",
            "Specific to the institution",
            ctx.institution.lower() in low or any((f.excerpt or "").lower()[:40] in low for f in used_facts if f.excerpt),
            f"Mention {ctx.institution} or a specific fact",
        )
    if ctx.kind == "sequence" and ctx.step == 1:
        add("fact", "Built on an approved research fact", bool(used_facts), "Select at least one approved fact")
    inferred = [f for f in used_facts if f.level == "source_inference"]
    if inferred:
        add(
            "qualified",
            "Inferences phrased as observations",
            any(h in low for h in HEDGES),
            "Use wording such as 'it looks like…' for source-supported inferences",
            blocking=False,
        )
    banned = [p for p in ctx.policy["banned_phrases"] if p.lower() in low]
    add("hype", "No hype or banned phrases", not banned, ", ".join(banned))
    rel = [p for p in FALSE_RELATIONSHIP if p in low]
    add("relationship", "Doesn't imply an existing relationship", not rel, ", ".join(rel))
    # Times and calendar dates (meeting proposals) are not claims; strip them before checking numbers.
    claim_text = TIME_DATE_RE.sub(" ", text)
    nums = [n for n in NUM_RE.findall(claim_text) if n.strip(",.") and n.strip(",.") not in sources]
    add("numbers", "No unsupported numbers or ROI claims", not nums, "Unsupported: " + ", ".join(sorted(set(nums))))
    restricted = [
        t
        for t in ctx.policy["restricted_terms"]
        if re.search(rf"\b{re.escape(t.lower())}\b", low) and t.lower() not in sources.lower()
    ]
    add("integrations", "No unapproved products, integrations or claims", not restricted, ", ".join(restricted))
    case_words = re.search(r"case study|we (?:have )?worked with|our (?:client|customer)s?\b|customers? like", low)
    add(
        "case_study",
        "Case studies only from the approved library",
        not case_words or any(c.kind == "case_study" for c in used_content),
        "Mentions a customer or case study that isn't approved content",
    )
    add("cta", "Clear call to action (a question)", "?" in body, "End with one low-friction question")
    add(
        "placeholders",
        "No unfinished placeholders",
        not re.search(r"\[EDIT|\{|\}|\[[A-Z ]+\]", text),
        "Replace the [EDIT …] parts",
    )
    add("opt_out", "Includes the opt-out line", ctx.policy["opt_out_line"] in full_body, "The opt-out line was removed")
    add(
        "subject",
        "Clear subject line",
        3 <= len(subject) <= 100 and "!" not in subject and not subject.isupper(),
        "3–100 characters, no exclamation marks or all caps",
    )
    return {"passed": all(c["passed"] for c in checks if c["blocking"]), "checks": checks}
