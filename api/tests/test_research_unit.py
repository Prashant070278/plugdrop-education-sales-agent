"""Unit tests: scoring, claim validation, mock provider, Claude reasoner (fake HTTP), rate limiter."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.research.providers import get_provider
from app.research.providers.base import SourceDocument
from app.research.reasoning import ClaudeReasoner, Finding, ReasonerError, RuleReasoner, validate_findings
from app.research.scoring import EvidenceIn, PersonIn, buying_role, score
from app.research.service import RateLimiter

NOW = datetime(2026, 10, 1, tzinfo=UTC)
INST = {"name": "Test U", "institution_type": "university", "student_size_range": "20k_50k"}


def ev(cat, level="verified_fact", days=30, conf=85, url="https://x.example/a"):
    return EvidenceIn("signal", cat, level, conf, NOW - timedelta(days=days) if days is not None else None, url)


def doc(doc_id="D1", text="The admissions office handled more than 20,000 enquiries by phone during the last intake.", **kw):
    return SourceDocument(
        doc_id=doc_id,
        url=f"https://u.example/{doc_id}",
        title="t",
        text=text,
        source_type=kw.get("source_type", "official_site"),
        retrieved_at=NOW,
        published_at=kw.get("published_at"),
        official=kw.get("official", True),
    )


# ---------------- scoring ----------------
def test_missing_information_scores_zero_and_is_listed():
    r = score(
        institution={"name": "Unknown", "institution_type": "other", "student_size_range": None},
        evidence=[],
        people=[],
        profile_confirmed=False,
        campus_count=None,
        now=NOW,
    )
    b = r.breakdown
    assert (
        b["problem"]["points"] == 0 and b["intent"]["points"] == 0 and b["access"]["points"] == 0 and b["quality"]["points"] == 0
    )
    assert b["fit"]["points"] == pytest.approx(0.3 * 0.2 * 25, abs=0.1)  # only the type ("other") counts
    items = {m["item"] for m in r.missing}
    assert {
        "Student numbers",
        "Number of campuses",
        "Official name and website",
        "Head of Admissions / Student Recruitment",
    } <= items
    assert r.tier == "D" and r.confidence == "low"
    assert "Not enough evidence" in r.next_action


def test_hypotheses_never_score():
    base = score(institution=INST, evidence=[], people=[], profile_confirmed=True, campus_count=3, now=NOW)
    hyp = score(
        institution=INST,
        evidence=[ev("enquiry_volume", "hypothesis"), ev("telephony_initiative", "hypothesis")],
        people=[],
        profile_confirmed=True,
        campus_count=3,
        now=NOW,
    )
    assert hyp.score == base.score
    assert any("hypothesis only" in r for r in hyp.breakdown["problem"]["reasons"])


def test_inference_counts_less_than_verified_fact():
    v = score(institution=INST, evidence=[ev("enquiry_volume")], people=[], profile_confirmed=True, campus_count=1, now=NOW)
    i = score(
        institution=INST,
        evidence=[ev("enquiry_volume", "source_inference")],
        people=[],
        profile_confirmed=True,
        campus_count=1,
        now=NOW,
    )
    assert v.breakdown["problem"]["points"] > i.breakdown["problem"]["points"] > 0


def test_stale_and_weak_evidence_is_penalised():
    fresh = score(
        institution=INST, evidence=[ev("crm_initiative", days=20)], people=[], profile_confirmed=True, campus_count=1, now=NOW
    )
    stale = score(
        institution=INST, evidence=[ev("crm_initiative", days=500)], people=[], profile_confirmed=True, campus_count=1, now=NOW
    )
    weak = score(
        institution=INST,
        evidence=[ev("crm_initiative", days=20, conf=20)],
        people=[],
        profile_confirmed=True,
        campus_count=1,
        now=NOW,
    )
    undated = score(
        institution=INST, evidence=[ev("crm_initiative", days=None)], people=[], profile_confirmed=True, campus_count=1, now=NOW
    )
    p = lambda r: r.breakdown["intent"]["points"]  # noqa: E731
    assert p(fresh) > p(undated) > p(stale) > 0
    assert p(fresh) > p(weak)
    assert any("stale" in x for x in stale.breakdown["quality"]["reasons"])


def test_weights_are_configurable_and_normalised():
    evidence = [ev("enquiry_volume"), ev("telephony_initiative")]
    people = [PersonIn("A B", "Director of Admissions", "published")]
    r = score(
        institution=INST,
        evidence=evidence,
        people=people,
        profile_confirmed=True,
        campus_count=3,
        now=NOW,
        stored_config={"weights": {"fit": 0, "problem": 50, "intent": 50, "access": 0, "quality": 0}},
    )
    assert r.breakdown["fit"]["points"] == 0 and r.breakdown["access"]["points"] == 0
    assert r.breakdown["problem"]["weight"] == 50
    r2 = score(
        institution=INST,
        evidence=evidence,
        people=people,
        profile_confirmed=True,
        campus_count=3,
        now=NOW,
        stored_config={"weights": {"fit": 1, "problem": 1, "intent": 1, "access": 1, "quality": 1}},
    )
    assert sum(b["weight"] for b in r2.breakdown.values()) == pytest.approx(100)


def test_decision_maker_access_and_roles():
    assert buying_role("Director of Admissions")[0] == "admissions_head"
    assert buying_role("Chief Information Officer")[0] == "cio_cto"
    assert buying_role("Contact Centre Manager")[0] == "contact_centre_head"
    assert buying_role("Professor of History")[0] is None
    named = score(
        institution=INST,
        evidence=[],
        people=[PersonIn("A", "Director of Admissions", "none")],
        profile_confirmed=True,
        campus_count=1,
        now=NOW,
    )
    verified = score(
        institution=INST,
        evidence=[],
        people=[PersonIn("A", "Director of Admissions", "provider_verified")],
        profile_confirmed=True,
        campus_count=1,
        now=NOW,
    )
    assert verified.breakdown["access"]["points"] > named.breakdown["access"]["points"] > 0
    assert any(m["item"] == "Business email for decision-makers" for m in named.missing)


def test_tiers_and_next_actions():
    strong = [ev(c) for c in ("enquiry_volume", "response_delays", "telephony_initiative", "crm_initiative", "intake_period")]
    r = score(
        institution=INST,
        evidence=strong,
        people=[PersonIn("A", "Director of Admissions", "provider_verified")],
        profile_confirmed=True,
        campus_count=4,
        now=NOW,
    )
    assert r.tier == "A" and r.confidence == "high" and "approve A" in r.next_action
    r2 = score(institution=INST, evidence=strong, people=[], profile_confirmed=True, campus_count=4, now=NOW)
    assert "no reachable decision-maker" in r2.next_action


# ---------------- claim validation ----------------
def test_validation_rules():
    d = doc()
    out = validate_findings(
        [
            Finding("signal", "enquiry_volume", "No source", "verified_fact", 90, None, None),
            Finding("signal", "enquiry_volume", "Fake quote", "verified_fact", 90, "D1", "handled 1,000,000 enquiries"),
            Finding("signal", "enquiry_volume", "Real quote", "verified_fact", 90, "D1", "handled more than 20,000 enquiries"),
            Finding("signal", "made_up_category", "x", "source_inference", 150, "D1", None),
            Finding("signal", "enquiry_volume", "Unknown doc", "verified_fact", 90, "D9", "x"),
        ],
        [d],
    )
    assert out[0].evidence_level == "hypothesis" and out[0].confidence <= 40
    assert out[1].evidence_level == "source_inference" and out[1].excerpt is None
    assert out[2].evidence_level == "verified_fact" and out[2].excerpt
    assert out[3].category == "other" and out[3].confidence == 100
    assert out[4].evidence_level == "hypothesis" and out[4].doc_id is None


def test_rule_reasoner_levels():
    docs = [
        doc(),
        doc(
            "D2",
            "Students reported waiting several days for replies to admission enquiries during peak season.",
            source_type="news",
            official=False,
        ),
    ]
    a = RuleReasoner().analyze({"name": "U", "institution_type_label": "university"}, docs, [])
    by = {(f.category, f.doc_id): f for f in a.findings if f.kind == "signal"}
    assert by[("enquiry_volume", "D1")].evidence_level == "verified_fact"
    assert by[("response_delays", "D2")].evidence_level == "source_inference"  # news is not treated as verified
    assert a.primary_use_case


# ---------------- mock provider ----------------
def test_mock_provider_only_fictional_and_deterministic():
    p = get_provider("mock")
    real = p.research(name="Real University", website="https://www.real-university.edu", country="", city="", roles=[])
    assert real.profile is None and real.documents == [] and real.people == []
    a = p.research(name="X U", website="https://x-u.example", country="India", city="Pune", roles=[])
    b = p.research(name="X U", website="https://x-u.example", country="India", city="Pune", roles=[])
    assert [d.text for d in a.documents] == [d.text for d in b.documents]
    assert all(d.url.split("/")[2].endswith(".example") for d in a.documents)
    assert all(person.linkedin_url is None for person in a.people)


# ---------------- Claude reasoner (fake HTTP) ----------------
def _claude(handler):
    s = Settings(anthropic_api_key="sk-test", anthropic_input_usd_per_mtok=3.0, anthropic_output_usd_per_mtok=15.0)
    return ClaudeReasoner(s, httpx.Client(base_url="https://api.anthropic.test", transport=httpx.MockTransport(handler)))


def test_claude_reasoner_parses_validates_and_costs():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.update(body=body, key=req.headers["x-api-key"])
        return httpx.Response(
            200,
            json={
                "usage": {"input_tokens": 2000, "output_tokens": 500},
                "content": [
                    {
                        "type": "tool_use",
                        "name": "record_analysis",
                        "input": {
                            "findings": [
                                {
                                    "kind": "signal",
                                    "category": "enquiry_volume",
                                    "claim": "High volumes",
                                    "evidence_level": "verified_fact",
                                    "confidence": 90,
                                    "doc_id": "D1",
                                    "excerpt": "handled more than 20,000 enquiries",
                                },
                                {
                                    "kind": "signal",
                                    "category": "crm_initiative",
                                    "claim": "Invented",
                                    "evidence_level": "verified_fact",
                                    "confidence": 90,
                                    "doc_id": "D1",
                                    "excerpt": "is replacing Salesforce",
                                },
                                {
                                    "kind": "signal",
                                    "category": "intake_period",
                                    "claim": "Guess",
                                    "evidence_level": "verified_fact",
                                    "confidence": 80,
                                    "doc_id": None,
                                    "excerpt": None,
                                },
                            ],
                            "fit_summary": "Fits.",
                            "primary_use_case": "voice_enquiry_automation",
                            "use_case_rationale": "Volumes.",
                            "missing_information": [{"item": "Intake dates", "how_to_verify": "Admissions page"}],
                        },
                    }
                ],
            },
        )

    a = _claude(handler).analyze({"name": "U"}, [doc()], [{"designation": "Director of Admissions"}])
    assert seen["key"] == "sk-test" and seen["body"]["tool_choice"]["name"] == "record_analysis"
    assert "Never output or guess email addresses" in seen["body"]["system"]
    levels = [f.evidence_level for f in a.findings]
    assert levels == ["verified_fact", "source_inference", "hypothesis"]
    assert a.input_tokens == 2000 and a.cost_usd == pytest.approx(2000 / 1e6 * 3 + 500 / 1e6 * 15)
    assert a.primary_use_case == "voice_enquiry_automation"


@pytest.mark.parametrize(("status", "transient"), [(529, True), (429, True), (500, True), (400, False), (401, False)])
def test_claude_reasoner_error_classification(status, transient):
    r = _claude(lambda req: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(ReasonerError) as e:
        r.analyze({"name": "U"}, [doc()], [])
    assert e.value.transient is transient


def test_rate_limiter_waits_when_bucket_empty():
    waits = []
    rl = RateLimiter()
    for _ in range(3):
        rl.wait("p", 2, sleep=waits.append)
    assert len(waits) == 1 and waits[0] > 0
