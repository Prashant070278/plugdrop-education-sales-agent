"""Explainable lead scoring (deterministic; no AI involved).

Five components, each scored 0..1 and multiplied by its configurable weight (default 25/25/20/15/15).
Rules that keep the score honest:
* missing information scores zero, never positive;
* hypotheses score zero; source-supported inferences count 0.6x a verified fact;
* evidence loses value as it ages and when its confidence is weak;
* every point awarded or withheld is explained in ``reasons``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.research.reasoning import SIGNAL_CATEGORIES

DEFAULT_CONFIG: dict[str, Any] = {
    "weights": {"fit": 25, "problem": 25, "intent": 20, "access": 15, "quality": 15},
    "tiers": {"A": 75, "B": 55, "C": 35},
    "evidence_fresh_days": 180,  # full value up to this age
    "evidence_stale_days": 365,  # strongly discounted beyond this age
    "rerun_after_days": 90,  # a research result older than this is "stale" and should be re-run
}
COMPONENT_LABELS = {
    "fit": "Institution fit",
    "problem": "Evidence of a relevant business problem",
    "intent": "Buying intent or timing",
    "access": "Access to a relevant decision-maker",
    "quality": "Evidence quality and freshness",
}
LEVEL_WEIGHT = {"verified_fact": 1.0, "source_inference": 0.6, "hypothesis": 0.0}
PROBLEM_WORTH = {"enquiry_volume": 1.0, "response_delays": 0.8, "hiring_contact_centre": 0.6, "multi_campus": 0.5}
INTENT_WORTH = {
    "telephony_initiative": 1.0,
    "crm_initiative": 0.9,
    "hiring_crm": 0.8,
    "new_campus": 0.7,
    "hiring_admissions": 0.7,
    "intake_period": 0.6,
    "digital_transformation": 0.6,
    "admissions_campaign": 0.5,
    "new_course": 0.5,
}
TYPE_FIT = {
    "university": 1.0,
    "university_group": 1.0,
    "online_education": 1.0,
    "edtech": 0.9,
    "test_prep": 0.9,
    "college": 0.9,
    "vocational": 0.6,
    "professional_education": 0.6,
    "other": 0.2,
}
SIZE_FIT = {"over_50k": 1.0, "20k_50k": 0.9, "5k_20k": 0.75, "1k_5k": 0.5, "under_1k": 0.25}
ROLE_RULES = [
    (r"admission|enrol", "admissions_head", "Head of Admissions", 1.0),
    (r"recruit", "student_recruitment_head", "Student Recruitment Head", 1.0),
    (r"contact cent|call cent", "contact_centre_head", "Contact Centre Head", 0.9),
    (r"\bcrm\b|digital|transformation", "crm_digital_head", "CRM / Digital Transformation Head", 0.9),
    (r"\bcio\b|\bcto\b|chief information|chief technology|it director|head of it", "cio_cto", "CIO / CTO", 0.8),
    (r"\bcoo\b|chief operating", "coo", "COO", 0.7),
]
ROLE_LABELS = {r[1]: r[2] for r in ROLE_RULES}
CONTACTABILITY = {"provider_verified": 1.0, "published": 0.9, "none": 0.5}


def buying_role(title: str | None) -> tuple[str | None, float]:
    t = (title or "").lower()
    for pattern, key, _, weight in ROLE_RULES:
        if re.search(pattern, t):
            return key, weight
    return None, 0.0


def merged_config(stored: dict[str, Any] | None) -> dict[str, Any]:
    cfg = {**DEFAULT_CONFIG, **(stored or {})}
    cfg["weights"] = {**DEFAULT_CONFIG["weights"], **(stored or {}).get("weights", {})}
    cfg["tiers"] = {**DEFAULT_CONFIG["tiers"], **(stored or {}).get("tiers", {})}
    return cfg


@dataclass
class EvidenceIn:
    kind: str
    category: str
    evidence_level: str
    confidence: int
    published_at: datetime | None
    source_url: str | None


@dataclass
class PersonIn:
    name: str
    designation: str | None
    email_status: str  # none | published | provider_verified


@dataclass
class ScoreResult:
    score: int
    tier: str
    confidence: str
    breakdown: dict[str, Any]
    missing: list[dict[str, str]]
    next_action: str
    decision_makers: list[dict[str, Any]] = field(default_factory=list)


def freshness(published_at: datetime | None, cfg: dict[str, Any], now: datetime) -> tuple[float, str]:
    if published_at is None:
        return 0.6, "undated"
    age = (now - published_at).days
    if age <= cfg["evidence_fresh_days"]:
        return 1.0, f"{age} days old"
    if age <= cfg["evidence_stale_days"]:
        return 0.7, f"{age} days old (ageing)"
    if age <= 2 * cfg["evidence_stale_days"]:
        return 0.35, f"{age} days old (stale)"
    return 0.15, f"{age} days old (very stale)"


def _value(e: EvidenceIn, cfg: dict[str, Any], now: datetime) -> float:
    fresh, _ = freshness(e.published_at, cfg, now)
    conf = 0.5 if e.confidence < 40 else max(0.4, e.confidence / 100)
    return LEVEL_WEIGHT.get(e.evidence_level, 0) * fresh * conf


def _category_component(
    evidence: list[EvidenceIn], worth: dict[str, float], divisor: float, cfg: dict[str, Any], now: datetime
) -> tuple[float, list[str]]:
    best: dict[str, tuple[float, EvidenceIn]] = {}
    for e in evidence:
        if e.kind != "signal" or e.category not in worth or e.evidence_level == "hypothesis":
            continue
        v = _value(e, cfg, now)
        if v > best.get(e.category, (0.0, e))[0]:
            best[e.category] = (v, e)
    reasons = []
    total = 0.0
    for cat, (v, e) in sorted(best.items(), key=lambda x: -x[1][0] * worth[x[0]]):
        total += v * worth[cat]
        _, age = freshness(e.published_at, cfg, now)
        level = "verified" if e.evidence_level == "verified_fact" else "inferred"
        reasons.append(f"+ {SIGNAL_CATEGORIES[cat]} ({level}, {age}, confidence {e.confidence}%)")
    hyps = {e.category for e in evidence if e.kind == "signal" and e.category in worth and e.evidence_level == "hypothesis"}
    for cat in sorted(hyps - set(best)):
        reasons.append(f"0 {SIGNAL_CATEGORIES[cat]}: hypothesis only — not counted")
    if not best:
        reasons.append("0 No supporting evidence found — missing information is not counted")
    return min(1.0, total / divisor), reasons


def score(
    *,
    institution: dict[str, Any],
    evidence: list[EvidenceIn],
    people: list[PersonIn],
    profile_confirmed: bool,
    campus_count: int | None,
    stored_config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ScoreResult:
    cfg = merged_config(stored_config)
    now = now or datetime.now(UTC)
    w = cfg["weights"]
    wsum = sum(max(0, float(v)) for v in w.values()) or 1.0
    norm = {k: max(0, float(v)) * 100 / wsum for k, v in w.items()}

    # --- fit
    fit_reasons: list[str] = []
    itype = institution.get("institution_type") or "other"
    t = TYPE_FIT.get(itype, 0.2)
    fit_reasons.append(f"{'+' if t >= 0.6 else '-'} Type: {itype.replace('_', ' ')} ({int(t * 100)}% match)")
    size: str = institution.get("student_size_range") or ""
    s = SIZE_FIT.get(size, 0.0)
    fit_reasons.append(f"+ Student size {size.replace('_', '–')}" if s else "0 Student size unknown — not counted")
    if campus_count is None:
        c = 0.0
        fit_reasons.append("0 Number of campuses unknown — not counted")
    else:
        c = 1.0 if campus_count >= 3 else 0.7 if campus_count == 2 else 0.3
        fit_reasons.append(f"+ {campus_count} campus{'es' if campus_count != 1 else ''}")
    p = 1.0 if profile_confirmed else 0.0
    fit_reasons.append(
        "+ Name and website confirmed on an official source" if p else "0 Name/website not confirmed by an official source"
    )
    fit = 0.3 * t + 0.3 * s + 0.2 * c + 0.2 * p

    problem, problem_reasons = _category_component(evidence, PROBLEM_WORTH, 1.5, cfg, now)
    intent, intent_reasons = _category_component(evidence, INTENT_WORTH, 1.8, cfg, now)

    # --- access
    makers: list[dict[str, Any]] = []
    for person in people:
        role, rw = buying_role(person.designation)
        if not role:
            continue
        contact = CONTACTABILITY.get(person.email_status, 0.5)
        makers.append(
            {
                "name": person.name,
                "designation": person.designation,
                "buying_role": role,
                "buying_role_label": ROLE_LABELS[role],
                "email_status": person.email_status,
                "value": rw * contact,
            }
        )
    makers.sort(key=lambda m: -m["value"])
    access = min(1.0, (makers[0]["value"] if makers else 0) + 0.25 * (makers[1]["value"] if len(makers) > 1 else 0))
    access_reasons = [
        f"+ {m['name']} — {m['buying_role_label']} ("
        + {
            "provider_verified": "email verified by provider",
            "published": "email published on source",
            "none": "named on source, no email",
        }[m["email_status"]]
        + ")"
        for m in makers[:4]
    ] or ["0 No relevant decision-maker identified"]

    # --- quality
    scored = [e for e in evidence if e.kind == "signal" and e.evidence_level != "hypothesis"]
    if scored:
        verified_share = sum(e.evidence_level == "verified_fact" for e in scored) / len(scored)
        fresh_avg = sum(freshness(e.published_at, cfg, now)[0] for e in scored) / len(scored)
        sources = len({e.source_url for e in scored if e.source_url})
        quality = 0.4 * verified_share + 0.4 * fresh_avg + 0.2 * min(1.0, sources / 3)
        stale = sum(freshness(e.published_at, cfg, now)[0] <= 0.35 for e in scored)
        quality_reasons = [
            f"{int(verified_share * 100)}% of scored evidence is verified fact",
            f"Average freshness {int(fresh_avg * 100)}%",
            f"{sources} distinct source{'s' if sources != 1 else ''}",
        ]
        if stale:
            quality_reasons.append(f"- {stale} stale item{'s' if stale != 1 else ''} discounted")
    else:
        quality, verified_share = 0.0, 0.0
        quality_reasons = ["0 No scored evidence"]

    ratios = {"fit": fit, "problem": problem, "intent": intent, "access": access, "quality": quality}
    reasons = {
        "fit": fit_reasons,
        "problem": problem_reasons,
        "intent": intent_reasons,
        "access": access_reasons,
        "quality": quality_reasons,
    }
    breakdown: dict[str, dict[str, Any]] = {
        k: {
            "label": COMPONENT_LABELS[k],
            "weight": round(norm.get(k, 0), 1),
            "ratio": round(ratios[k], 3),
            "points": round(ratios[k] * norm.get(k, 0), 1),
            "reasons": reasons[k],
        }
        for k in COMPONENT_LABELS
    }
    total = int(round(sum(float(b["points"]) for b in breakdown.values())))
    tiers = cfg["tiers"]
    tier = "A" if total >= tiers["A"] else "B" if total >= tiers["B"] else "C" if total >= tiers["C"] else "D"

    if len(scored) < 2:
        confidence = "low"
    elif verified_share >= 0.6 and len(scored) >= 3 and makers:
        confidence = "high"
    else:
        confidence = "medium"

    # --- missing information (never scored) and how to verify it
    missing: list[dict[str, str]] = []
    if not size:
        missing.append(
            {"item": "Student numbers", "how_to_verify": "Official prospectus, annual report or national regulator data"}
        )
    if campus_count is None:
        missing.append({"item": "Number of campuses", "how_to_verify": "Official 'campuses' or 'contact us' page"})
    if not profile_confirmed:
        missing.append(
            {
                "item": "Official name and website",
                "how_to_verify": "Confirm on the institution's own website or regulator listing",
            }
        )
    roles_found = {m["buying_role"] for m in makers}
    if not roles_found & {"admissions_head", "student_recruitment_head"}:
        missing.append(
            {
                "item": "Head of Admissions / Student Recruitment",
                "how_to_verify": "Official staff directory, or a licensed B2B contact provider",
            }
        )
    if makers and not any(m["email_status"] != "none" for m in makers):
        missing.append(
            {
                "item": "Business email for decision-makers",
                "how_to_verify": "Licensed contact provider with verification; never infer from an email pattern",
            }
        )
    if not any(e.category in ("intake_period", "admissions_campaign") for e in scored):
        missing.append({"item": "Next intake dates", "how_to_verify": "Official admissions or 'apply' page"})
    if scored and not any(e.published_at for e in scored):
        missing.append({"item": "Publication dates for evidence", "how_to_verify": "Check dates on the cited pages"})

    name = institution.get("name", "this institution")
    if not scored:
        action = "Not enough evidence to qualify. Re-run with a licensed data source or research manually."
    elif tier in ("A", "B") and makers and makers[0]["value"] >= 0.5:
        action = (
            f"Review the evidence, approve {makers[0]['name']} ({makers[0]['buying_role_label']}) and push to the CRM; "
            "then move the lead to Qualified."
        )
    elif tier in ("A", "B"):
        action = f"Strong fit but no reachable decision-maker: identify the Head of Admissions at {name} via the official staff directory or a licensed contact provider."
    elif tier == "C":
        action = f"Nurture: re-run research in {cfg['rerun_after_days']} days to catch new intake, campus or hiring signals."
    else:
        action = "Deprioritise for now; no action until new signals appear."

    return ScoreResult(
        score=max(0, min(100, total)),
        tier=tier,
        confidence=confidence,
        breakdown=breakdown,
        missing=missing,
        next_action=action,
        decision_makers=[{k: v for k, v in m.items() if k != "value"} for m in makers],
    )
