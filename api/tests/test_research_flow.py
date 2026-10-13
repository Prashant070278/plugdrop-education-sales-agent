"""Integration tests: queue -> provider -> reasoner -> score -> review -> push into the CRM."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import Settings
from app.db import SessionLocal
from app.models import Contact, ResearchFinding, ResearchRun
from app.research.reasoning import ReasonerError
from app.research.service import process_next
from tests.conftest import new_contact, new_institution


def drain(settings: Settings | None = None, **kw) -> list[ResearchRun]:
    done = []
    with SessionLocal() as db:
        while (r := process_next(db, settings=settings, sleep=lambda s: None, **kw)) is not None:
            done.append(r)
            if r.status == "queued":  # retry scheduled or budget deferral; stop instead of spinning
                break
    return done


def make_due():
    with SessionLocal() as db:
        db.execute(update(ResearchRun).where(ResearchRun.status == "queued").values(next_attempt_at=datetime.now(UTC)))
        db.commit()


def demo_inst(client, slug="meridian-college-commerce", name="Meridian College of Commerce", **kw):
    return new_institution(client, name=name, website=f"https://www.{slug}.example", student_size_range="20k_50k", **kw)


def researched(client, inst_id):
    job = client.post("/api/research/jobs", json={"institution_ids": [inst_id]})
    assert job.status_code == 202, job.text
    drain()
    data = client.get(f"/api/research/institutions/{inst_id}").json()
    assert data["result"], data
    return data["result"]


def test_full_workflow_research_review_push(rep, manager):
    inst = demo_inst(rep)
    lead = rep.post("/api/leads", json={"institution_id": inst["id"], "score": 10}).json()
    r = researched(rep, inst["id"])

    # result shape the brief asks for
    assert 0 <= r["score"] <= 100 and r["tier"] in "ABCD" and r["confidence"] in ("low", "medium", "high")
    s = r["summary"]
    assert s["fit_summary"] and s["next_action"] and s["primary_use_case_label"]
    assert isinstance(s["missing_information"], list) and s["decision_makers"]
    assert set(r["score_breakdown"]) == {"fit", "problem", "intent", "access", "quality"}
    assert sum(c["weight"] for c in r["score_breakdown"].values()) == 100
    assert all(e["source_url"] for e in r["evidence"] if e["evidence_level"] != "hypothesis")
    assert all(e["retrieved_at"] for e in r["evidence"] if e["source_url"])
    assert r["cost_usd"] is not None and r["provider_calls"] >= 1
    assert any(entry["event"] == "succeeded" for entry in r["log"])

    # institution summary + timeline entry
    i2 = rep.get(f"/api/institutions/{inst['id']}").json()
    assert i2["research_score"] == r["score"] and i2["research_tier"] == r["tier"] and i2["last_researched_at"]
    tl = rep.get("/api/activities", params={"institution_id": inst["id"]}).json()["items"]
    assert any(a["subject"].startswith("Automated research") for a in tl)

    # review: hypotheses cannot be approved
    hyp = [e for e in r["evidence"] if e["evidence_level"] == "hypothesis"]
    if hyp:
        assert rep.post(f"/api/research/runs/{r['id']}/review", json={"evidence": {hyp[0]["id"]: "approved"}}).status_code == 422
    sourced = [e for e in r["evidence"] if e["evidence_level"] != "hypothesis"]
    approve = {e["id"]: "approved" for e in sourced[:-1]}
    reject = {sourced[-1]["id"]: "rejected"}
    cands = {c["id"]: "approved" for c in r["candidates"]}
    rv = rep.post(f"/api/research/runs/{r['id']}/review", json={"evidence": {**approve, **reject}, "candidates": cands})
    assert rv.status_code == 200

    # push
    p = rep.post(f"/api/research/runs/{r['id']}/push").json()
    assert p["findings"] == len(approve)
    assert p["contacts_created"] + p["contacts_linked"] + len(p["skipped"]) == len(cands)
    with SessionLocal() as db:
        rfs = db.scalars(select(ResearchFinding).where(ResearchFinding.institution_id == inst["id"])).all()
        assert len(rfs) == len(approve)
        assert all(
            f.research_evidence_id and f.source_url and f.evidence_level in ("verified_fact", "source_inference") for f in rfs
        )
        assert all(f.lead_id == lead["id"] for f in rfs)
        contacts = db.scalars(select(Contact).where(Contact.institution_id == inst["id"])).all()
        assert all(c.source and c.source.startswith("Research agent") for c in contacts)
    assert rep.get(f"/api/leads/{lead['id']}").json()["score"] == r["score"]
    # pushing again creates nothing new; reviewed items are locked
    again = rep.post(f"/api/research/runs/{r['id']}/push").json()
    assert again["findings"] == 0 and again["contacts_created"] == 0
    assert rep.post(f"/api/research/runs/{r['id']}/review", json={"evidence": {sourced[0]["id"]: "rejected"}}).status_code == 409
    # audit trail
    hist = manager.get("/api/audit", params={"entity_type": "research_run"}).json()["items"]
    assert {"push", "review"} <= {h["action"] for h in hist}


def test_emails_are_never_invented(rep):
    """Pattern-guessed emails from a provider are discarded; only published or provider-verified emails are kept."""
    # Scan several fictional institutions so every email mode appears.
    ids = [demo_inst(rep, slug=f"inst-{i}", name=f"Inst {i} University")["id"] for i in range(8)]
    rep.post("/api/research/jobs", json={"institution_ids": ids})
    drain()
    seen_discarded = False
    for iid in ids:
        r = rep.get(f"/api/research/institutions/{iid}").json()["result"]
        for c in r["candidates"]:
            if c["email"]:
                assert c["email_status"] in ("published", "provider_verified")
                if c["email_status"] == "published":
                    assert c["email"] in (c["excerpt"] or "").lower()
            else:
                assert c["email_status"] == "none"
            if c["notes"] and "pattern-based email; discarded" in c["notes"]:
                seen_discarded = True
                assert c["email"] is None
            assert c["verification_status"] == ("verified" if c["email_status"] == "provider_verified" else "unverified")
            assert c["source_url"]
    assert seen_discarded, "fixture should include at least one pattern-guessed email"


def test_real_institution_gets_no_invented_data(rep):
    inst = new_institution(rep, name="Some Real University", website="https://www.some-real-university.edu")
    r = researched(rep, inst["id"])
    assert r["evidence"] == [] and r["candidates"] == []
    assert r["tier"] == "D" and r["summary"]["missing_information"][0]["item"] == "Any source material"


def test_transient_failure_is_retried_then_succeeds(rep):
    inst = demo_inst(rep, slug="flaky-college", name="Flaky College")
    job = rep.post("/api/research/jobs", json={"institution_ids": [inst["id"]]}).json()
    first = drain()
    assert first[-1].status == "queued" and first[-1].attempts == 1 and "timeout" in first[-1].last_error
    assert first[-1].next_attempt_at > datetime.now(UTC)  # back-off
    make_due()
    drain()
    d = rep.get(f"/api/research/jobs/{job['id']}").json()
    run = d["runs"][0]
    assert d["status"] == "completed" and run["status"] == "succeeded" and run["attempts"] == 2
    full = rep.get(f"/api/research/runs/{run['id']}").json()
    assert [e["event"] for e in full["log"]].count("retry_scheduled") == 1


def test_permanent_failure_marks_job_partial_and_can_be_retried(rep):
    ok = demo_inst(rep)
    bad = demo_inst(rep, slug="blocked-institute", name="Blocked Institute")
    job = rep.post("/api/research/jobs", json={"institution_ids": [ok["id"], bad["id"]]}).json()
    drain()
    d = rep.get(f"/api/research/jobs/{job['id']}").json()
    assert d["status"] == "partial" and d["counts"] == {"succeeded": 1, "failed": 1}
    failed = next(r for r in d["runs"] if r["status"] == "failed")
    assert failed["attempts"] == 1 and "not permitted" in failed["last_error"]
    assert rep.post(f"/api/research/runs/{failed['id']}/retry").json()["status"] == "queued"


def test_reasoner_errors_are_retried_and_crashes_contained(rep):
    inst = demo_inst(rep)
    rep.post("/api/research/jobs", json={"institution_ids": [inst["id"]]})

    class Down:
        name = "down"

        def analyze(self, *a):
            raise ReasonerError("Claude API temporarily unavailable (529)")

    class Crash:
        name = "crash"

        def analyze(self, *a):
            raise RuntimeError("boom")

    r1 = drain(reasoner=Down())[-1]
    assert r1.status == "queued" and "529" in r1.last_error
    make_due()
    r2 = drain(reasoner=Crash())[-1]
    assert r2.status == "queued" and "RuntimeError" in r2.last_error
    make_due()
    r3 = drain(reasoner=Crash())[-1]
    assert r3.status == "failed" and r3.attempts == 3
    with SessionLocal() as db:  # nothing half-written from failed attempts
        assert db.scalar(select(ResearchRun).where(ResearchRun.id == r3.id)).evidence == []


def test_daily_budget_defers_work(rep):
    inst = demo_inst(rep)
    rep.post("/api/research/jobs", json={"institution_ids": [inst["id"]]})
    r = drain(settings=Settings(research_daily_budget_usd=0))[-1]
    assert r.status == "queued" and r.attempts == 0 and "budget" in r.last_error
    assert r.next_attempt_at > datetime.now(UTC) + timedelta(hours=0)


def test_job_validation_and_duplicates(rep):
    inst = demo_inst(rep)
    assert rep.post("/api/research/jobs", json={"institution_ids": []}).status_code == 422
    assert rep.post("/api/research/jobs", json={"institution_ids": [99999]}).status_code == 404
    assert rep.post("/api/research/jobs", json={"institution_ids": [inst["id"]]}).status_code == 202
    assert rep.post("/api/research/jobs", json={"institution_ids": [inst["id"]]}).status_code == 409


def test_cancel_job(rep, manager):
    a, b = demo_inst(rep), demo_inst(rep, slug="other-u", name="Other U")
    job = rep.post("/api/research/jobs", json={"institution_ids": [a["id"], b["id"]]}).json()
    d = rep.post(f"/api/research/jobs/{job['id']}/cancel").json()
    assert d["status"] == "cancelled" and d["counts"] == {"cancelled": 2}
    assert drain() == []


def test_suppressed_and_existing_contacts_on_push(rep):
    inst = demo_inst(rep)
    r = researched(rep, inst["id"])
    cands = r["candidates"]
    assert cands
    # 1) an existing CRM contact with the same email is linked, not duplicated
    with_email = next((c for c in cands if c["email"]), None)
    if with_email:
        existing = new_contact(
            rep, inst["id"], first=with_email["first_name"], last=with_email["last_name"], email=with_email["email"]
        )
    # 2) suppress the whole domain afterwards -> remaining new contacts are skipped
    rep.post(f"/api/research/runs/{r['id']}/review", json={"candidates": {c["id"]: "approved" for c in cands}})
    rep.post("/api/consent", json={"domain": "meridian-college-commerce.example", "status": "suppressed"})
    p = rep.post(f"/api/research/runs/{r['id']}/push").json()
    assert p["contacts_created"] == 0
    assert len(p["skipped"]) == len(cands)
    if with_email:
        assert rep.get("/api/contacts", params={"q": with_email["email"]}).json()["total"] == 1
        _ = existing


def test_rerun_supersedes_old_result_and_stale_rerun(rep, manager):
    inst = demo_inst(rep)
    first = researched(rep, inst["id"])
    second = researched(rep, inst["id"])
    assert second["id"] != first["id"]
    assert rep.post(f"/api/research/runs/{first['id']}/push").status_code == 409
    assert rep.get(f"/api/research/runs/{first['id']}").json()["is_latest"] is False
    # make it stale
    with SessionLocal() as db:
        db.execute(update(ResearchRun).values(finished_at=datetime.now(UTC) - timedelta(days=200)))
        from app.models import Institution

        db.execute(update(Institution).values(last_researched_at=datetime.now(UTC) - timedelta(days=200)))
        db.commit()
    assert rep.get(f"/api/research/runs/{second['id']}").json()["is_stale"] is True
    ov = rep.get("/api/research/overview").json()
    assert ov["stale"] >= 1 and ov["provider"] == "mock" and ov["reasoner"] == "rules"
    job = rep.post("/api/research/jobs/stale", json={"limit": 10})
    assert job.status_code == 202 and job.json()["label"].startswith("Re-run stale")


def test_config_permissions_validation_and_rescore(rep, manager):
    inst = demo_inst(rep)
    r = researched(rep, inst["id"])
    cfg = rep.get("/api/research/config").json()
    assert cfg["weights"] == {"fit": 25, "problem": 25, "intent": 20, "access": 15, "quality": 15}
    assert rep.put("/api/research/config", json=cfg).status_code == 403
    bad = {**cfg, "tiers": {"A": 50, "B": 60, "C": 10}}
    assert manager.put("/api/research/config", json=bad).status_code == 422
    new = {**cfg, "weights": {"fit": 100, "problem": 0, "intent": 0, "access": 0, "quality": 0}}
    res = manager.put("/api/research/config", json=new).json()
    assert res["rescored"] >= 1
    after = rep.get(f"/api/research/runs/{r['id']}").json()
    assert after["weights_used"]["fit"] == 100
    assert after["score"] == round(after["score_breakdown"]["fit"]["ratio"] * 100)
    assert after["score_breakdown"]["problem"]["points"] == 0
