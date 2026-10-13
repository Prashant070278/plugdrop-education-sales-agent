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
    r"\b(unsubscribe|remove me|take me off|stop (?:emailing|contacting|sending)|do not (?:email|contact)|don't (?:email|contact)|opt(?:-| )?out|no more emails)\b",
    re.I,
)
RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "out_of_office",
        re.compile(
            r"out of (?:the )?office|automatic reply|auto-?reply|on (?:annual )?leave|away (?:until|from)|limited access to email",
            re.I,
        ),
    ),
    (
        "not_interested",
        re.compile(
            r"not interested|no thanks|no, thank|not (?:a )?(?:fit|relevant)|we(?:'re| are) not looking|please don't|already have a (?:solution|vendor|provider)",
            re.I,
        ),
    ),
    (
        "referral",
        re.compile(
            r"(?:right|best) person|(?:contact|reach out to|speak (?:to|with)|copying|cc'?ing|looping in)\s+(?:my colleague|[A-Z][a-z]+)|forwarded (?:this|your)",
            re.I,
        ),
    ),
    (
        "meeting_request",
        re.compile(
            r"(?:book|schedule|set up|arrange)\s+(?:a\s+)?(?:call|meeting|demo|time)|(?:available|free) (?:on|next|this)|send (?:me )?(?:an? )?(?:invite|calendar)|let'?s (?:talk|meet|speak)|monday|tuesday|wednesday|thursday|friday",
            re.I,
        ),
    ),
    (
        "not_now",
        re.compile(
            r"not (?:right )?now|next (?:quarter|year|term|semester|month)|after (?:our|the) intake|later (?:this|in the) year|no budget|revisit|circle back|check back",
            re.I,
        ),
    ),
    (
        "needs_information",
        re.compile(
            r"more (?:information|details|info)|send (?:me|over|us) (?:some|more|a|the)|brochure|pricing|how (?:does|would|much)|case stud|what does it cost|which (?:crm|systems)",
            re.I,
        ),
    ),
    (
        "interested",
        re.compile(
            r"interested|sounds (?:good|interesting|useful)|tell me more|keen to|would like to (?:learn|know|hear)|happy to (?:chat|talk|explore)",
            re.I,
        ),
    ),
]


@dataclass
class Classification:
    label: str
    confidence: int
    opt_out: bool
    summary: str
    source: str


def classify_rules(subject: str, body: str) -> Classification:
    text = f"{subject}\n{body}"
    opt = bool(OPT_OUT.search(text))
    if opt:
        return Classification("not_interested", 95, True, "Asked not to be contacted again.", "rules")
    for label, pattern in RULES:
        if pattern.search(text):
            return Classification(label, 70, False, f"Matched wording for '{label.replace('_', ' ')}'.", "rules")
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


def classify(subject: str, body: str, settings: Settings, client: httpx.Client | None = None) -> Classification:
    rules = classify_rules(subject, body)
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
                "system": "Classify the reply to a B2B sales email. Use 'unclear' when unsure. Never guess opt_out=false if they ask to stop.",
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
