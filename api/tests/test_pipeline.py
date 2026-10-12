from tests.conftest import new_contact, new_institution


def setup_lead(client, stage="new"):
    inst = new_institution(client)
    contact = new_contact(client, inst["id"])
    r = client.post("/api/leads", json={"institution_id": inst["id"], "primary_contact_id": contact["id"], "stage": stage})
    assert r.status_code == 201, r.text
    return inst, contact, r.json()


def test_one_open_lead_per_institution(rep):
    inst, _, _ = setup_lead(rep)
    assert rep.post("/api/leads", json={"institution_id": inst["id"]}).status_code == 409


def test_stage_change_logs_timeline_and_audit(rep):
    _, _, lead = setup_lead(rep)
    r = rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "researched", "note": "Found admissions team size"})
    assert r.status_code == 200 and r.json()["stage"] == "researched"
    tl = rep.get(f"/api/leads/{lead['id']}/timeline").json()
    assert tl[0]["activity_type"] == "status_change" and tl[0]["details"] == {"from": "new", "to": "researched"}
    assert tl[0]["body"] == "Found admissions team size"
    hist = rep.get(f"/api/audit/entity/lead/{lead['id']}").json()
    assert hist[0]["action"] == "stage_change"


def test_lost_requires_reason(rep):
    _, _, lead = setup_lead(rep)
    assert rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "lost"}).status_code == 422
    r = rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "lost", "lost_reason": "No budget"})
    assert r.json()["lost_reason"] == "No budget"


def test_closed_lead_allows_new_open_lead(rep):
    inst, _, lead = setup_lead(rep)
    rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "lost", "lost_reason": "Timing"})
    assert rep.post("/api/leads", json={"institution_id": inst["id"]}).status_code == 201
    # Re-opening the old lead now clashes with the new open one.
    assert rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "nurture"}).status_code == 409


def test_suppressed_contact_blocks_outreach(rep):
    _, contact, lead = setup_lead(rep, stage="qualified")
    assert (
        rep.post("/api/consent", json={"contact_id": contact["id"], "status": "opted_out", "reason": "Asked"}).status_code == 201
    )
    assert rep.get(f"/api/contacts/{contact['id']}").json()["suppressed"] is True
    r = rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "outreach_ready"})
    assert r.status_code == 409 and "opted out" in r.json()["detail"]
    r = rep.post("/api/activities", json={"activity_type": "email", "subject": "Hi", "contact_id": contact["id"]})
    assert r.status_code == 409
    # Notes are still allowed.
    assert (
        rep.post("/api/activities", json={"activity_type": "note", "subject": "FYI", "contact_id": contact["id"]}).status_code
        == 201
    )


def test_domain_suppression_applies_to_everyone_at_institution(rep, manager):
    inst, contact, _ = setup_lead(rep)
    assert rep.post("/api/consent", json={"domain": "test-uni.example", "status": "suppressed"}).status_code == 201
    assert rep.get(f"/api/contacts/{contact['id']}").json()["suppressed"] is True
    assert rep.get(f"/api/institutions/{inst['id']}").json()["suppressed"] is True
    # Only managers can record consent or remove a suppression.
    assert rep.post("/api/consent", json={"domain": "test-uni.example", "status": "consented"}).status_code == 403
    rec = rep.get("/api/consent").json()["items"][0]
    assert rep.delete(f"/api/consent/{rec['id']}").status_code == 403
    assert manager.delete(f"/api/consent/{rec['id']}").status_code == 204


def test_outreach_requires_primary_contact(rep):
    inst = new_institution(rep)
    lead = rep.post("/api/leads", json={"institution_id": inst["id"]}).json()
    assert rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": "contacted"}).status_code == 422


def test_primary_contact_must_belong_to_institution(rep):
    a = new_institution(rep)
    b = new_institution(rep, name="B Uni", website="https://b.example")
    c = new_contact(rep, b["id"], email="x@b.example")
    assert rep.post("/api/leads", json={"institution_id": a["id"], "primary_contact_id": c["id"]}).status_code == 422


def test_opportunities_and_board_filters(rep, manager):
    _, _, lead = setup_lead(rep)
    r = rep.post(
        "/api/opportunities",
        json={
            "lead_id": lead["id"],
            "name": "Voice AI rollout",
            "estimated_value": "25000.50",
            "solution_interest": ["voice_ai", "crm"],
            "expected_decision_date": "2030-01-15",
        },
    )
    assert r.status_code == 201
    opp = r.json()
    assert opp["institution"]["id"] == lead["institution"]["id"]
    assert rep.post("/api/opportunities", json={"lead_id": lead["id"], "name": "Bad", "estimated_value": -1}).status_code == 422
    r = rep.patch(f"/api/opportunities/{opp['id']}", json={"stage": "proposal", "next_action": "Send proposal"})
    assert r.json()["stage"] == "proposal"
    assert rep.get("/api/opportunities", params={"stage": "proposal"}).json()["total"] == 1
    assert rep.get(f"/api/leads/{lead['id']}").json()["open_value"] == "25000.50"
    tl = rep.get(f"/api/leads/{lead['id']}/timeline").json()
    assert any("discovery → proposal" in a["subject"] for a in tl)
    assert rep.delete(f"/api/opportunities/{opp['id']}").status_code == 403
    assert manager.delete(f"/api/opportunities/{opp['id']}").status_code == 204


def test_research_finding_appears_on_timeline(rep):
    inst, _, lead = setup_lead(rep)
    r = rep.post(
        "/api/research",
        json={
            "institution_id": inst["id"],
            "lead_id": lead["id"],
            "category": "Pain point",
            "summary": "Slow enquiry response",
            "source_url": "https://test-uni.example/news",
            "confidence": 70,
        },
    )
    assert r.status_code == 201
    tl = rep.get(f"/api/leads/{lead['id']}/timeline").json()
    assert tl[0]["activity_type"] == "research"
    assert len(rep.get(f"/api/leads/{lead['id']}/research").json()) == 1
