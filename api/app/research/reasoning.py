"""Turn retrieved source documents into classified findings.

A *reasoner* reads only the documents a provider retrieved and proposes findings. It does not
retrieve anything itself, and every finding it returns is checked by ``validate_findings``:

* a finding that cites no retrieved document becomes a ``hypothesis`` (never scored);
* a ``verified_fact`` must quote an excerpt that really appears in the cited document's text,
  otherwise it is downgraded to ``source_inference`` and its excerpt is removed;
* categories outside the known list become ``other``; confidence is clamped to 0-100.

Two reasoners exist: ``ClaudeReasoner`` (Claude API, used for summarisation and classification)
and ``RuleReasoner`` (deterministic keyword rules, used in tests and when no API key is set).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.research.providers.base import SourceDocument

log = logging.getLogger("plugdrop.research")

SIGNAL_CATEGORIES = {
    "enquiry_volume": "High enquiry volumes",
    "multi_campus": "Multi-campus admissions operation",
    "response_delays": "Slow responses to enquiries",
    "new_campus": "New campus launch",
    "new_course": "New course launch",
    "intake_period": "Upcoming intake period",
    "admissions_campaign": "Admissions campaign",
    "hiring_admissions": "Hiring admissions staff",
    "hiring_contact_centre": "Hiring contact-centre staff",
    "hiring_crm": "Hiring CRM / digital roles",
    "crm_initiative": "CRM transformation initiative",
    "telephony_initiative": "Telephony / contact-centre initiative",
    "digital_transformation": "Digital transformation project",
    "other": "Other",
}
PROFILE_CATEGORIES = {"official_name", "website", "location", "institution_type", "campus_count"}
USE_CASES = {
    "voice_enquiry_automation": "Voice AI enquiry handling",
    "enquiry_qualification": "Enquiry qualification",
    "counselling": "AI counselling assistant",
    "missed_call_recovery": "Missed-call recovery",
    "follow_up": "Automated follow-up",
    "whatsapp_engagement": "WhatsApp engagement",
    "crm_integration": "CRM integration",
}
LEVELS = ("verified_fact", "source_inference", "hypothesis")


@dataclass
class Finding:
    kind: str  # profile | signal | use_case
    category: str
    claim: str
    evidence_level: str
    confidence: int
    doc_id: str | None = None
    excerpt: str | None = None


@dataclass
class Analysis:
    findings: list[Finding]
    fit_summary: str
    primary_use_case: str | None
    use_case_rationale: str
    missing_information: list[dict[str, str]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


class ReasonerError(Exception):
    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


class Reasoner(Protocol):
    name: str

    def analyze(self, institution: dict[str, Any], documents: list[SourceDocument], people: list[dict[str, Any]]) -> Analysis: ...


# ---------------------------------------------------------------------------
# validation shared by every reasoner
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def validate_findings(findings: list[Finding], documents: list[SourceDocument]) -> list[Finding]:
    by_id = {d.doc_id: d for d in documents}
    out: list[Finding] = []
    for f in findings:
        f.confidence = max(0, min(100, int(f.confidence)))
        if f.evidence_level not in LEVELS:
            f.evidence_level = "hypothesis"
        if f.kind == "signal" and f.category not in SIGNAL_CATEGORIES:
            f.category = "other"
        if f.kind == "use_case" and f.category not in USE_CASES:
            continue
        if f.kind == "profile" and f.category not in PROFILE_CATEGORIES:
            continue
        doc = by_id.get(f.doc_id) if f.doc_id else None
        if doc is None:
            f.doc_id, f.excerpt, f.evidence_level = None, None, "hypothesis"
            f.confidence = min(f.confidence, 40)
        elif f.excerpt and _norm(f.excerpt) not in _norm(doc.text):
            # Quoted text not found in the source: do not present it as a quote or a verified fact.
            f.excerpt = None
            if f.evidence_level == "verified_fact":
                f.evidence_level = "source_inference"
            f.confidence = min(f.confidence, 50)
        elif f.evidence_level == "verified_fact" and not f.excerpt:
            f.evidence_level = "source_inference"
        if not f.claim.strip():
            continue
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# deterministic rule-based reasoner
# ---------------------------------------------------------------------------
RULES: list[tuple[str, str, str]] = [
    # (regex, category, claim template). Primary sources -> verified_fact; news/forums -> source_inference.
    (r"handled more than [\d,]+ enquiries[^.]*\.", "enquiry_volume", "Reports high admissions enquiry volumes"),
    (r"will open a new campus[^.]*\.", "new_campus", "Opening a new campus"),
    (r"launches a new [^.]*programme[^.]*\.", "new_course", "Launching a new programme"),
    (r"Applications for the [^.]*intake close[^.]*\.", "intake_period", "Has an upcoming intake deadline"),
    (r"hiring \d+ Admissions Counsellors[^.]*\.", "hiring_admissions", "Hiring admissions counsellors"),
    (r"recruiting a CRM Manager[^.]*\.", "hiring_crm", "Hiring a CRM Manager"),
    (
        r"request for proposals for a contact centre telephony and CRM upgrade\.",
        "telephony_initiative",
        "Running a procurement for contact-centre telephony and CRM",
    ),
    (r"digital transformation programme[^.]*\.", "digital_transformation", "Announced a digital transformation programme"),
    (r"waiting several days for replies[^.]*\.", "response_delays", "Applicants report slow replies to enquiries"),
    (r"launched its \d{4} admissions campaign[^.]*\.", "admissions_campaign", "Running an admissions campaign"),
    (r"operating \d+ campuses\.", "multi_campus", "Operates multiple campuses"),
]
# Secondary inferences drawn from a primary signal.
INFERENCES = {
    "hiring_admissions": ("enquiry_volume", "Hiring several counsellors suggests high enquiry volumes", 55),
    "hiring_crm": ("crm_initiative", "Hiring a CRM Manager to migrate enquiries suggests a CRM change is under way", 60),
    "telephony_initiative": ("crm_initiative", "The procurement includes a CRM upgrade", 60),
}
USE_CASE_MAP = {
    "enquiry_volume": ["voice_enquiry_automation", "enquiry_qualification", "missed_call_recovery"],
    "response_delays": ["missed_call_recovery", "follow_up", "voice_enquiry_automation"],
    "intake_period": ["enquiry_qualification", "follow_up", "counselling"],
    "admissions_campaign": ["enquiry_qualification", "follow_up"],
    "hiring_admissions": ["voice_enquiry_automation", "counselling"],
    "hiring_contact_centre": ["voice_enquiry_automation", "missed_call_recovery"],
    "telephony_initiative": ["voice_enquiry_automation", "missed_call_recovery", "crm_integration"],
    "hiring_crm": ["crm_integration"],
    "crm_initiative": ["crm_integration"],
    "digital_transformation": ["crm_integration"],
    "multi_campus": ["enquiry_qualification", "crm_integration"],
    "new_campus": ["enquiry_qualification", "voice_enquiry_automation"],
    "new_course": ["enquiry_qualification", "follow_up"],
}
PRIMARY_SOURCES = {"official_site", "press_release", "job_posting", "licensed_data"}


def use_case_ranking(findings: list[Finding]) -> list[tuple[str, float, list[str], str | None]]:
    """Rank use cases by the scored evidence supporting them (hypotheses excluded).
    Returns (use_case, weight, supporting signal labels, doc_id of the strongest support)."""
    totals: dict[str, float] = {}
    support: dict[str, list[str]] = {}
    best_doc: dict[str, tuple[float, str | None]] = {}
    for f in findings:
        if f.kind != "signal" or f.evidence_level == "hypothesis":
            continue
        w = 1.0 if f.evidence_level == "verified_fact" else 0.6
        for i, uc in enumerate(USE_CASE_MAP.get(f.category, [])):
            v = w * (1 - 0.2 * i)
            totals[uc] = totals.get(uc, 0) + v
            support.setdefault(uc, []).append(SIGNAL_CATEGORIES[f.category])
            if v > best_doc.get(uc, (0.0, None))[0]:
                best_doc[uc] = (v, f.doc_id)
    return sorted(((k, v, support[k], best_doc[k][1]) for k, v in totals.items()), key=lambda x: -x[1])


class RuleReasoner:
    name = "rules"

    def analyze(self, institution: dict[str, Any], documents: list[SourceDocument], people: list[dict[str, Any]]) -> Analysis:
        findings: list[Finding] = []
        for d in documents:
            primary = d.official or d.source_type in PRIMARY_SOURCES
            for pattern, cat, claim in RULES:
                m = re.search(pattern, d.text)
                if not m:
                    continue
                start = d.text.rfind(".", 0, m.start()) + 1
                excerpt = d.text[start : m.end()].strip()
                level = "verified_fact" if primary else "source_inference"
                findings.append(Finding("signal", cat, claim, level, 85 if primary else 60, d.doc_id, excerpt))
                if cat in INFERENCES:
                    icat, iclaim, conf = INFERENCES[cat]
                    findings.append(Finding("signal", icat, iclaim, "source_inference", conf, d.doc_id, excerpt))
                if "WhatsApp" in d.text:
                    findings.append(
                        Finding(
                            "use_case",
                            "whatsapp_engagement",
                            "Enquiries already arrive over WhatsApp",
                            "source_inference",
                            60,
                            d.doc_id,
                            excerpt if "WhatsApp" in excerpt else None,
                        )
                    )
        if not any(f.category == "enquiry_volume" for f in findings):
            findings.append(
                Finding(
                    "signal",
                    "enquiry_volume",
                    "Admissions teams in this segment typically face seasonal call peaks (not evidenced)",
                    "hypothesis",
                    30,
                )
            )
        ranking = use_case_ranking(findings)
        for uc, _, why, doc_id in ranking[:3]:
            findings.append(
                Finding(
                    "use_case",
                    uc,
                    f"{USE_CASES[uc]} — supported by: {', '.join(sorted(set(why))).lower()}",
                    "source_inference",
                    60,
                    doc_id,
                )
            )
        seen: set[tuple[str, str]] = set()
        unique: list[Finding] = []
        for f in findings:
            key = (f.kind, f.category) if f.kind == "use_case" else (f.kind, f"{f.category}|{f.doc_id}|{f.evidence_level}")
            if key not in seen:
                seen.add(key)
                unique.append(f)
        findings = validate_findings(unique, documents)
        primary_uc = ranking[0][0] if ranking else None
        signals = sorted(
            {SIGNAL_CATEGORIES[f.category] for f in findings if f.kind == "signal" and f.evidence_level != "hypothesis"}
        )
        kind = institution.get("institution_type_label", "education provider")
        article = "an" if kind[:1].lower() in "aeiou" else "a"
        fit = f"{institution['name']} is {article} {kind}" + (
            f" with evidence of: {', '.join(signals).lower()}." if signals else " with no supporting evidence found yet."
        )
        rationale = (
            f"Most supported by {', '.join(sorted(set(ranking[0][2]))).lower()}."
            if ranking
            else "Not enough evidence to choose a use case."
        )
        return Analysis(
            findings=findings, fit_summary=fit, primary_use_case=primary_uc, use_case_rationale=rationale, model="rules-v1"
        )


# ---------------------------------------------------------------------------
# Claude API reasoner
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You analyse source documents about an education institution for PlugDrop.AI, which sells Voice AI + CRM \
(enquiry handling, qualification, counselling, missed-call recovery, follow-up, WhatsApp engagement, CRM integration).

Rules:
- Use ONLY the documents provided. Never add facts from memory. Never output or guess email addresses or phone numbers.
- Every finding must cite one doc_id and quote an exact excerpt copied verbatim from that document's text.
- evidence_level: "verified_fact" only when the document directly states the claim; "source_inference" when the claim is a
  reasonable inference from the cited text; "hypothesis" for ideas with no supporting document (doc_id null). Hypotheses never count as evidence.
- Treat news and forum reports as source_inference unless the institution itself states it.
- Missing information is not evidence. List it under missing_information with how it could be verified.
- Record the tool output exactly once."""

TOOL = {
    "name": "record_analysis",
    "description": "Record the classified findings for this institution.",
    "input_schema": {
        "type": "object",
        "required": ["findings", "fit_summary", "primary_use_case", "use_case_rationale", "missing_information"],
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "category", "claim", "evidence_level", "confidence"],
                    "properties": {
                        "kind": {"enum": ["profile", "signal", "use_case"]},
                        "category": {
                            "type": "string",
                            "description": "signal: "
                            + ", ".join(SIGNAL_CATEGORIES)
                            + "; use_case: "
                            + ", ".join(USE_CASES)
                            + "; profile: "
                            + ", ".join(sorted(PROFILE_CATEGORIES)),
                        },
                        "claim": {"type": "string"},
                        "evidence_level": {"enum": list(LEVELS)},
                        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                        "doc_id": {"type": ["string", "null"]},
                        "excerpt": {"type": ["string", "null"]},
                    },
                },
            },
            "fit_summary": {"type": "string"},
            "primary_use_case": {"type": ["string", "null"], "enum": [*USE_CASES, None]},
            "use_case_rationale": {"type": "string"},
            "missing_information": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["item", "how_to_verify"],
                    "properties": {"item": {"type": "string"}, "how_to_verify": {"type": "string"}},
                },
            },
        },
    },
}


class ClaudeReasoner:
    name = "claude"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.s = settings
        self.client = client or httpx.Client(base_url=settings.anthropic_base_url, timeout=settings.anthropic_timeout_seconds)

    def analyze(self, institution: dict[str, Any], documents: list[SourceDocument], people: list[dict[str, Any]]) -> Analysis:
        payload_docs = [
            {
                "doc_id": d.doc_id,
                "url": d.url,
                "title": d.title,
                "source_type": d.source_type,
                "official": d.official,
                "published_at": d.published_at.date().isoformat() if d.published_at else None,
                "text": d.text[:6000],
            }
            for d in documents[:40]
        ]
        user = json.dumps(
            {"institution": institution, "documents": payload_docs, "people_found_titles": [p["designation"] for p in people]},
            ensure_ascii=False,
        )
        body = {
            "model": self.s.anthropic_model,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "record_analysis"},
            "messages": [{"role": "user", "content": user}],
        }
        try:
            r = self.client.post(
                "/v1/messages",
                json=body,
                headers={
                    "x-api-key": self.s.anthropic_api_key or "",
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise ReasonerError(f"Claude API network error: {e.__class__.__name__}") from e
        if r.status_code in (429, 500, 502, 503, 504, 529):
            raise ReasonerError(f"Claude API temporarily unavailable ({r.status_code})", transient=True)
        if r.status_code >= 400:
            raise ReasonerError(f"Claude API error {r.status_code}: {r.text[:200]}", transient=False)
        data = r.json()
        usage = data.get("usage", {})
        tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        cost = tin / 1e6 * self.s.anthropic_input_usd_per_mtok + tout / 1e6 * self.s.anthropic_output_usd_per_mtok
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if not block:
            raise ReasonerError("Claude API returned no structured analysis", transient=True)
        out = block.get("input", {})
        findings = []
        for f in out.get("findings", []):
            try:
                findings.append(
                    Finding(
                        kind=f["kind"],
                        category=f["category"],
                        claim=str(f["claim"])[:1000],
                        evidence_level=f["evidence_level"],
                        confidence=int(f.get("confidence", 50)),
                        doc_id=f.get("doc_id"),
                        excerpt=(f.get("excerpt") or None),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        findings = validate_findings(findings, documents)
        primary = out.get("primary_use_case") if out.get("primary_use_case") in USE_CASES else None
        missing = [
            {"item": str(m.get("item", ""))[:300], "how_to_verify": str(m.get("how_to_verify", ""))[:300]}
            for m in out.get("missing_information", [])
            if isinstance(m, dict)
        ]
        return Analysis(
            findings=findings,
            fit_summary=str(out.get("fit_summary", ""))[:2000],
            primary_use_case=primary,
            use_case_rationale=str(out.get("use_case_rationale", ""))[:1000],
            missing_information=missing,
            input_tokens=tin,
            output_tokens=tout,
            cost_usd=cost,
            model=self.s.anthropic_model,
        )


def get_reasoner(settings: Settings) -> Reasoner:
    choice = settings.research_reasoner
    if choice == "auto":
        choice = "claude" if settings.anthropic_api_key else "rules"
    if choice == "claude":
        return ClaudeReasoner(settings)
    if choice in ("rules", "mock"):
        return RuleReasoner()
    raise ValueError(f"Unknown research reasoner '{settings.research_reasoner}'")
