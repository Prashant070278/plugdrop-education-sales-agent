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
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings

HEDGES = ("looks like", "seems", "appears", "may ", "might", "if i've", "correct me", "i understand", "it sounds")
FALSE_RELATIONSHIP = [
    r"as (?:we )?discussed",
    r"as promised",
    r"following up on our (?:conversation|call|meeting|discussion|chat)",
    r"(?:great|nice|good|lovely) (?:speaking|talking|chatting|to meet|meeting|to speak|to talk|to catch up)",
    r"per our (?:call|conversation|discussion|meeting)",
    r"\bwe (?:spoke|talked|met|discussed)\b",
    r"when we (?:met|spoke|talked)",
    r"last time we",
    r"you requested",
    r"(?:our|your) (?:existing |ongoing |long-standing )?(?:partnership|relationship|association)",
    r"as you know",
    r"remember me",
    r"(?:was|were|been|i was|i've been) (?:referred|recommended|introduced)",
    r"(?:your|a) colleague (?:suggested|recommended|referred|asked|mentioned)",
    r"\byou (?:signed up|downloaded|registered|subscribed|asked for|enquired)\b",
    r"\breconnect|\bcatch up again|touching base again",
]
# Only true after an earlier email or a reply, so blocked in a first email.
FIRST_EMAIL_ONLY = [
    r"(?:my|our) (?:previous|last|earlier) (?:e-?mail|note|message)",
    r"following up on my",
    r"\bthanks? (?:you )?for (?:your interest|reaching out|getting (?:in touch|back)|your (?:enquiry|inquiry|reply|response)|contacting us)",
]
HYPE = re.compile(
    r"game[\s-]*chang|revolution|disrupt|cutting[\s-]*edge|best[\s-]*in[\s-]*class|world[\s-]*class|skyrocket|guarantee"
    r"|\b\d+x\b|unparalleled|unmatched|state[\s-]*of[\s-]*the[\s-]*art|next[\s-]*gen|world'?s first|#1\b|number one|supercharg"
    r"|!!|\bmagic(?:al)?\b|mind[\s-]*blowing|incredible|amazing|unbeatable|no[\s-]*brainer",
    re.I,
)
NUM_WORDS = re.compile(
    r"\bper ?cent\b|\bdoubl(?:e|ed|es|ing)\b|\btripl(?:e|ed|es|ing)\b|\btwice\b|\bhalve[sd]?\b|\bhalf the\b|\b\w+-fold\b"
    r"|\blakhs?\b|\bcrores?\b|\b(?:hundreds|thousands|millions) of\b|\broi\b|return on investment|pays? for itself|cost savings?",
    re.I,
)
CUSTOMER_REF = re.compile(
    r"case study|we (?:have )?worked with|our (?:client|customer|partner)s?\b|customers? like|clients? like|trusted by|used by"
    r"|(?:universities|colleges|institutions|institutes|schools|campuses|groups) (?:like|such as)|already use(?:s)? (?:us|plugdrop)"
    r"|work(?:s|ed|ing)? with (?:many|several|leading|top|other)"
    r"|\b(?:institutions|universities|colleges|customers|clients|schools|teams) (?:use|using|rely on|trust|choose|chose|love)\b",
    re.I,
)
INSTITUTION_NAME = re.compile(
    r"\b(?:[A-Z][\w.&'-]*\s+){1,5}(?:University|College|Institute|Vidyapeeth|Vishwavidyalaya|Academy|School)\b"
    r"|\b(?:University|Institute|College) of(?:\s+[A-Z][\w.&'-]*){1,4}"
)
INTEGRATION = re.compile(r"\bintegrat\w*|\bconnects? (?:with|to|into) your\b|\bplugs? (?:into|in to)\b|\bsyncs? with\b", re.I)
PRODUCT_CLAIM = re.compile(
    r"\b(?:plugdrop(?:'s)?|our (?:agents?|ai|voice ai|platform|product|solution|system|bot|tool|software)|it|we)\b[^.?!\n]{0,80}?"
    r"\b(?:can|will|does|handles?|answers?|speaks?|supports?|integrates?|reduces?|increases?|improves?|cuts?|boosts?|automates?"
    r"|delivers?|offers?|provides?|enables?|guarantees?|ensures?|eliminates?|replaces?|understands?)\b",
    re.I,
)
# Internal research vocabulary must never reach a prospect.
INTERNAL_NOTES = re.compile(
    r"\btrigger\s*:|\binference\b|\bpositioning\b|\(verify|\bunverified\b|\(reported\)|->|\bnot found\b|\bworkbook\b"
    r"|\bPD-\d{3}\b|opportunity signal|buying role|\bconfidence (?:high|medium|low)\b|\bresearch (?:note|fact) F\d+",
    re.I,
)
INVISIBLE = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad]")
STOP = set(
    [
        "that",
        "this",
        "with",
        "your",
        "from",
        "have",
        "will",
        "they",
        "them",
        "their",
        "there",
        "what",
        "when",
        "which",
        "about",
        "would",
        "could",
        "should",
        "into",
        "also",
        "each",
        "more",
        "most",
        "such",
        "than",
        "then",
        "very",
        "just",
        "only",
        "some",
        "many",
        "over",
        "been",
        "were",
        "being",
        "does",
        "done",
        "here",
        "make",
        "made",
    ]
)


def _normalise(text: str) -> str:
    return INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


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


def usable_facts(facts: list[Fact]) -> list[Fact]:
    """Facts the deterministic writer can turn into a sentence with approved wording. Research notes in other
    categories are never pasted into an email: they are internal notes, not customer-facing language."""
    return sorted((f for f in facts if f.category in CATEGORY_PHRASES), key=lambda f: (f.level != "verified_fact", f.category))


def _observation(ctx: Context, f: Fact) -> str:
    verified, inferred = CATEGORY_PHRASES[f.category]
    if f.level == "verified_fact":
        host = _host(f.source_url)
        return f"I saw on {host} that {verified}." if host else f"I read that {verified}."
    return f"From what I've read, it looks like {inferred} — please correct me if I've got that wrong."


# ---------------------------------------------------------------------------
# deterministic writer
# ---------------------------------------------------------------------------
class RuleWriter:
    name = "rules"

    def write(self, ctx: Context) -> Draft:
        if ctx.kind == "reply":
            return self._reply(ctx)
        if not ctx.facts:
            raise WriterError("No approved research facts for this institution. Approve and push research findings first.")
        facts = usable_facts(ctx.facts)
        vp = _pick(ctx.content, "value_prop", ctx.use_case)
        cta = _pick(ctx.content, "cta", ctx.use_case)
        if vp is None or cta is None:
            missing = [n for n, c in (("value proposition", vp), ("call to action", cta)) if c is None]
            raise WriterError(
                f"The content library has no approved {' or '.join(missing)} for this use case. Ask an admin to add and approve one."
            )
        inst = ctx.institution
        if ctx.step == 1:
            subject = (ctx.template or {}).get("subject") or USE_CASE_SUBJECT.get(ctx.use_case or "", "Admissions at {inst}")
            if facts:
                fact = facts[0]
                opening = _observation(ctx, fact)
                why = f"Step 1 leads with the strongest fact ({fact.category}, {fact.level}) and the approved value proposition for {ctx.use_case}."
            else:
                # No fact has approved wording: a person writes the opening line from the research note.
                fact = sorted(ctx.facts, key=lambda f: f.level != "verified_fact")[0]
                opening = f"[EDIT: one specific, true sentence about {inst}, based on research fact {fact.id}]"
                why = (
                    f"None of the approved facts can be worded automatically, so the opening line is left for you. "
                    f'Research note {fact.id}: "{fact.text}" — write it in your own words; don\'t paste the note.'
                )
            body = f"Dear {ctx.first_name},\n\n{opening}\n\n{vp.body}\n\n{cta.body}"
            used_f, used_c = [fact.id], [vp.id, cta.id]
        else:
            # Follow-ups use a different fact where possible and stay shorter.
            # A different fact from the first email; if there isn't one, don't repeat the same observation.
            follow_fact: Fact | None = facts[ctx.step - 1] if len(facts) >= ctx.step else None
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
            use_case_study = bool(case and ctx.step == 2)
            middle = case.body if (case and use_case_study) else (_observation(ctx, follow_fact) if follow_fact else "")
            if ctx.step >= ctx.policy.get("max_attempts", 3):
                body = (
                    f"Dear {ctx.first_name},\n\nI don't want to crowd your inbox, so this is my last note on this. "
                    f"{middle}\n\n{alt_cta.body}"
                )
            else:
                body = f"Dear {ctx.first_name},\n\nFollowing up on my note about enquiries at {inst}. {middle}\n\n{alt_cta.body}"
            body = body.replace(" \n\n", "\n\n")
            used_f = [follow_fact.id] if (follow_fact and not use_case_study) else []
            used_c = [alt_cta.id] + ([case.id] if case and use_case_study else [])
            why = f"Follow-up {ctx.step} adds {'an approved case study' if use_case_study else 'a different fact' if follow_fact else 'a softer call to action'}."
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
- Fact texts are internal research notes: never quote or paste them, never mention words like "trigger", "inference",
  "verify" or "positioning", and never name other institutions or vendors unless the fact or approved content does.
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
NUM_RE = re.compile(r"\b\d+(?:\.\d+)?x\b|\b\d[\d,]*(?:\.\d+)?%?", re.I)
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
TIME_DATE_RE = re.compile(
    rf"\b\d{{1,2}}(?::\d{{2}})?\s?(?:am|pm)\b|\b\d{{1,2}}:\d{{2}}\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\b"
    rf"|\b(?:{MONTHS})[a-z]*\s+\d{{1,2}}(?:st|nd|rd|th)?\b",
    re.I,
)


def _nums(text: str) -> set[str]:
    return {n.lower().rstrip(",.") for n in NUM_RE.findall(text)}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", text.lower()) if w not in STOP}


def quality_check(ctx: Context, subject: str, full_body: str, facts_used: list[str], content_used: list[str]) -> dict[str, Any]:
    """Return {"passed": bool, "checks": [...]}. Blocking failures prevent approval.

    The checks are a safety net, not a guarantee: a person still reads and approves every email."""
    raw_subject = subject
    subject, full_body = _normalise(subject), _normalise(full_body)
    body = strip_footer(ctx, full_body)
    text = f"{subject}\n{body}"
    low = text.lower()
    sources = " ".join(
        [f.text + " " + (f.excerpt or "") for f in ctx.facts]
        + [c.body + " " + c.title for c in ctx.content]
        + [ctx.institution, ctx.contact_name]
    )
    approved_text = " ".join([c.body for c in ctx.content] + [f.text + " " + (f.excerpt or "") for f in ctx.facts])
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
    notes = sorted({m.group(0) for m in INTERNAL_NOTES.finditer(text)})
    add("internal", "No internal research notes", not notes, "Rewrite in plain language: " + ", ".join(notes))
    banned = [p for p in ctx.policy["banned_phrases"] if p.lower() in low] + [m.group(0) for m in HYPE.finditer(text)]
    add("hype", "No hype or banned phrases", not banned, ", ".join(sorted(set(banned))))
    rel_patterns = FALSE_RELATIONSHIP + (FIRST_EMAIL_ONLY if (ctx.kind == "sequence" and ctx.step == 1) else [])
    if ctx.kind == "sequence" and ctx.step > 1:
        rel_patterns = rel_patterns + [FIRST_EMAIL_ONLY[2]]  # "thanks for your interest/reply" only fits a reply
    rel = [m.group(0) for p in rel_patterns for m in [re.search(p, low)] if m]
    add("relationship", "Doesn't imply an existing relationship", not rel, ", ".join(rel))
    # Times and calendar dates (meeting proposals) are not claims; strip them before checking numbers.
    claim_text = TIME_DATE_RE.sub(" ", text)
    source_nums = _nums(sources)
    nums = sorted(n for n in _nums(claim_text) if n and n not in source_nums)
    num_words = sorted(
        {m.group(0).lower() for m in NUM_WORDS.finditer(claim_text)} - {w.lower() for w in NUM_WORDS.findall(sources)}
    )
    add(
        "numbers",
        "No unsupported numbers or ROI claims",
        not nums and not num_words,
        "Unsupported: " + ", ".join(nums + num_words),
    )
    restricted = [
        t
        for t in ctx.policy["restricted_terms"]
        if re.search(rf"\b{re.escape(t.lower())}\b", low) and t.lower() not in sources.lower()
    ]
    integ = [m.group(0) for m in INTEGRATION.finditer(text) if not INTEGRATION.search(approved_text)]
    add(
        "integrations",
        "No unapproved products, integrations or claims",
        not restricted and not integ,
        ", ".join(restricted + integ),
    )
    target = ctx.institution.lower()
    others = sorted(
        {
            n.strip()
            for n in INSTITUTION_NAME.findall(text)
            if n.strip().lower() not in target and target not in n.strip().lower() and n.strip().lower() not in sources.lower()
        }
    )
    case_words = CUSTOMER_REF.search(low)
    add(
        "case_study",
        "Case studies and customer names only from the approved library",
        not others and (not case_words or any(c.kind == "case_study" for c in used_content)),
        "Mentions " + (", ".join(others) if others else "a customer or case study") + " that isn't in approved content",
    )
    approved_words = _words(approved_text)
    unsupported_claims = []
    for sentence in re.split(r"(?<=[.?!])\s+|\n+", body):
        if PRODUCT_CLAIM.search(sentence) and not sentence.strip().endswith("?"):
            w = _words(sentence)
            if w and len(w & approved_words) / len(w) < 0.6:
                unsupported_claims.append(sentence.strip()[:80])
    add(
        "claims",
        "Product capabilities only from approved content",
        not unsupported_claims,
        "Use approved wording for: " + " | ".join(unsupported_claims),
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
        3 <= len(subject) <= 100 and "!" not in subject and not subject.isupper() and not re.search(r"[\r\n]", raw_subject),
        "3–100 characters on one line, no exclamation marks or all caps",
    )
    return {"passed": all(c["passed"] for c in checks if c["blocking"]), "checks": checks}
