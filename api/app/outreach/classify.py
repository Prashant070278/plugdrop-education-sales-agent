"""Classify prospect replies. Opt-out wording is always detected by rules, whatever classifier runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from app.config import Settings

LABELS = (
    "interested",
    "meeting_request",
    "needs_information",
    "not_now",
    "not_interested",
    "referral",
    "out_of_office",
    "unclear",
)
OPT_OUT = re.compile(
    r"\bunsubscribe\b|\bopt(?:-| )?out\b|\bremove (?:me|us|my|our)\b|\btake (?:me|us|my|our)\b[^.\n]{0,30}\boff\b"
    r"|\bstop (?:e-?mailing|mailing|sending|contacting|spamming)\b|\bspam(?:ming)?\b|\bno (?:more|further) (?:e-?mails?|mails?)\b"
    r"|\b(?:do not|don'?t|dont|never|pls don'?t|please do not)\s+(?:e-?mail|mail|contact|spam|write to)\b"
    r"|\b(?:do not|don'?t|dont|never|pls don'?t|please do not)\s+send\b[^.\n]{0,25}\b(?:e-?mails?|mails?|messages?)\b"
    r"|\bnot to (?:e-?mail|mail|contact)\b|\bmail mat\b|\bmat bhej",
    re.I,
)
# Automatic replies: out-of-office notices and "we have received your query" acknowledgements. These don't stop
# follow-ups (they only pause them), because no person has read the email yet.
AUTO = re.compile(
    r"out of (?:the )?office|out of station|automatic reply|auto-?reply|auto-?generated|automated (?:reply|response|message)"
    r"|on (?:annual |medical )?leave|on vacation|travell?ing (?:till|until|and)|away (?:until|till|from)"
    r"|limited access to (?:e-?mail|mail)|do not reply to this|(?:query|enquiry|inquiry|request|mail|e-?mail) has been received"
    r"|we have received your (?:e-?mail|mail|query|enquiry|inquiry|message)|ticket (?:no|number|id)\b"
    r"|will (?:get back|respond|revert) to you (?:within|shortly|soon)",
    re.I,
)
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("out_of_office", AUTO),
    (
        "referral",
        re.compile(
            r"(?:right|best|correct|concerned) person|forwarded (?:this|your|the)|marked (?:it |this |your mail )?to|looping in|copying|cc'?ing|copied"
            r"|(?:contact|reach out to|write to|connect with|speak (?:to|with)|get in touch with)\s+(?:my colleague|our|the|mr\.?|ms\.?|mrs\.?|dr\.?|prof\.?|[A-Z][a-z]+)"
            r"[^.\n]{0,40}(?:head|in-?charge|officer|director|registrar|team|department|office|manager|colleague|coordinator)|\bin-?charge\b",
            re.I,
        ),
    ),
    (
        "not_now",
        re.compile(
            r"not (?:right )?now|not at (?:present|the moment|this (?:time|stage))|currently not|abhi nahi|baad me"
            r"|next (?:quarter|year|term|semester|month|session|academic year|admission cycle|cycle)"
            r"|after (?:our |the )?(?:intake|admissions?|session|exams?|semester|counselling)|later (?:this|in the) year"
            r"|no budget|revisit|circle back|check back|(?:connect|contact|reach out|get in touch|touch base|write) (?:again )?(?:after|in|later|next)",
            re.I,
        ),
    ),
    (
        "not_interested",
        re.compile(
            r"not interested|no thanks|no, thank|not (?:a )?(?:fit|relevant)|not required|not needed|no requirement|no need"
            r"|(?:we(?:'re| are)|i(?:'m| am)) not looking|not for us|already (?:using|use|have|working with|got)|we use [A-Z]"
            r"|(?:signed|gone|going|tied up) with (?:another|a different|an?other) (?:vendor|provider|company)|another vendor"
            r"|have (?:our|an) (?:own|in-?house)|in-?house (?:team|solution|system)|not in a position|we will pass",
            re.I,
        ),
    ),
    (
        "meeting_request",
        re.compile(
            r"(?:book|schedule|set up|arrange|fix)\s+(?:a\s+)?(?:call|meeting|demo|time|slot)|(?:available|free) (?:on|next|this|tomorrow|today)"
            r"|send (?:me |us )?(?:an? )?(?:invite|calendar|meeting link)|let'?s (?:talk|meet|speak|connect)|call me|give (?:me |us )?a (?:call|ring)"
            r"|(?:connect|talk|speak|meet|discuss) (?:on|at|tomorrow|today|this|next)|\btomorrow\b|day after|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b"
            r"|google meet|zoom|teams (?:call|link|meeting)|monday|tuesday|wednesday|thursday|friday|saturday",
            re.I,
        ),
    ),
    (
        "needs_information",
        re.compile(
            r"more (?:information|details|info)|(?:share|send)\b[^.\n]{0,25}\b(?:details|information|info|brochure|deck|presentation|proposal|quotation|quote|commercials|pricing|price|cost|demo video|video|company profile|client list|references)"
            r"|brochure|pricing|commercials|quotation|how (?:does|do|would|much|can)|what (?:does|is|are) (?:it|the)|case stud|which (?:crm|systems)",
            re.I,
        ),
    ),
    (
        "interested",
        re.compile(
            r"interest(?:ed|ing)|sounds (?:good|interesting|useful)|tell me more|keen|would like to (?:learn|know|hear|understand|see)"
            r"|happy to (?:chat|talk|explore|connect|discuss)|let'?s (?:explore|discuss)|yes,? please|go ahead|looks (?:useful|good|relevant)"
            r"|will (?:connect|get in touch|contact you|call you|reach out)",
            re.I,
        ),
    ),
]

QUOTE_START = re.compile(
    r"^\s*(?:On\b.{0,200}?\bwrote:|-{2,}\s*Original Message\s*-{2,}|_{5,}|From:\s.+|Sent from my\b.*|-----\s*Forwarded)",
    re.I | re.S | re.M,
)


def strip_quoted(body: str, own_lines: tuple[str, ...] = ()) -> str:
    """Only the prospect's own words: cut the quoted original email, '>' lines and our own footer lines."""
    m = QUOTE_START.search(body)
    text = body[: m.start()] if m and m.start() > 0 else body
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))
    for own in own_lines:
        if own:
            text = text.replace(own, " ")
    return text.strip() or body.strip()


@dataclass
class Classification:
    label: str
    confidence: int
    opt_out: bool
    summary: str
    source: str


def classify_rules(subject: str, body: str, own_lines: tuple[str, ...] = ()) -> Classification:
    text = f"{re.sub(r'^(?:re|fw|fwd)\s*:\s*', '', subject, flags=re.I)}\n{strip_quoted(body, own_lines)}"
    if OPT_OUT.search(text):
        return Classification("not_interested", 95, True, "Asked not to be contacted again.", "rules")
    for label, pattern in RULES:
        if pattern.search(text):
            summary = (
                "Automatic reply (out of office or acknowledgement) — follow-ups are paused, not stopped."
                if label == "out_of_office"
                else f"Matched wording for '{label.replace('_', ' ')}'."
            )
            return Classification(label, 70, False, summary, "rules")
    return Classification("unclear", 30, False, "No clear intent detected; please read the reply.", "rules")


TOOL = {
    "name": "classify_reply",
    "description": "Classify a prospect's reply to a sales email.",
    "input_schema": {
        "type": "object",
        "required": ["label", "confidence", "opt_out", "summary"],
        "properties": {
            "label": {"enum": list(LABELS)},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "opt_out": {"type": "boolean", "description": "true if they ask not to be contacted again"},
            "summary": {"type": "string", "maxLength": 300},
        },
    },
}


def classify(
    subject: str, body: str, settings: Settings, client: httpx.Client | None = None, own_lines: tuple[str, ...] = ()
) -> Classification:
    rules = classify_rules(subject, body, own_lines)
    body = strip_quoted(body, own_lines)
    if not settings.anthropic_api_key or settings.outreach_writer == "rules":
        return rules
    client = client or httpx.Client(base_url=settings.anthropic_base_url, timeout=settings.anthropic_timeout_seconds)
    try:
        r = client.post(
            "/v1/messages",
            headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": settings.anthropic_model,
                "max_tokens": 300,
                "tools": [TOOL],
                "tool_choice": {"type": "tool", "name": "classify_reply"},
                "system": (
                    "Classify the reply to a B2B sales email sent to an Indian university or college. Use 'unclear' when "
                    "unsure. Use 'out_of_office' for any automatic reply, including 'we have received your query' "
                    "acknowledgements. Set opt_out=true whenever they ask not to be emailed or contacted again."
                ),
                "messages": [{"role": "user", "content": json.dumps({"subject": subject, "body": body[:6000]})}],
            },
        )
        r.raise_for_status()
        block = next(b for b in r.json()["content"] if b.get("type") == "tool_use")["input"]
        label = block["label"] if block.get("label") in LABELS else "unclear"
        c = Classification(
            label, int(block.get("confidence", 50)), bool(block.get("opt_out")), str(block.get("summary", ""))[:300], "claude"
        )
    except Exception:  # noqa: BLE001 - classification must never block reply handling
        return rules
    if rules.opt_out:  # opt-out wording always wins
        c.opt_out, c.label = True, "not_interested"
    return c
