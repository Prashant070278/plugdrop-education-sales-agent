from datetime import UTC, datetime, timedelta

from tests.conftest import new_contact, new_institution


def test_task_lifecycle_and_permissions(rep, manager):
    inst = new_institution(rep)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    r = rep.post("/api/tasks", json={"title": "Call registrar", "due_at": past, "priority": "high", "institution_id": inst["id"]})
    assert r.status_code == 201
    task = r.json()
    assert task["overdue"] is True and task["institution_name"] == "Test University"
    assert rep.get("/api/tasks", params={"due": "overdue", "mine": True}).json()["total"] == 1
    r = rep.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert r.json()["status"] == "done" and r.json()["completed_at"] and r.json()["overdue"] is False
    mtask = manager.post("/api/tasks", json={"title": "Manager task"}).json()
    assert rep.patch(f"/api/tasks/{mtask['id']}", json={"title": "hijack"}).status_code == 403
    assert rep.delete(f"/api/tasks/{mtask['id']}").status_code == 403
    assert manager.patch(f"/api/tasks/{task['id']}", json={"priority": "low"}).status_code == 200


def test_reminder_must_precede_due(rep):
    now = datetime.now(UTC)
    r = rep.post(
        "/api/tasks", json={"title": "x", "due_at": now.isoformat(), "reminder_at": (now + timedelta(hours=1)).isoformat()}
    )
    assert r.status_code == 422


def test_dashboard_metrics(rep):
    inst = new_institution(rep)
    c = new_contact(rep, inst["id"])
    lead = rep.post("/api/leads", json={"institution_id": inst["id"], "primary_contact_id": c["id"]}).json()
    for s in ["researched", "qualified", "outreach_ready", "contacted", "engaged", "meeting_booked"]:
        assert rep.post(f"/api/leads/{lead['id']}/stage", json={"stage": s}).status_code == 200
    rep.post("/api/activities", json={"activity_type": "call", "subject": "Call", "lead_id": lead["id"], "contact_id": c["id"]})
    rep.post("/api/activities", json={"activity_type": "meeting", "subject": "Demo", "lead_id": lead["id"]})
    rep.post("/api/opportunities", json={"lead_id": lead["id"], "name": "Deal", "estimated_value": 10000})
    b = new_institution(rep, name="Lost Uni", website="https://lost.example")
    lost = rep.post("/api/leads", json={"institution_id": b["id"]}).json()
    rep.post(f"/api/leads/{lost['id']}/stage", json={"stage": "lost", "lost_reason": "No fit"})

    d = rep.get("/api/dashboard").json()
    assert d["total_prospects"] == 2
    assert d["qualified_leads"] == 1
    assert d["outreach_activity"] == 1 and d["meetings"] == 1
    assert d["pipeline_value"] == "10000.00"
    assert d["win_rate"] == 0.0
    funnel = {f["stage"]: f["reached"] for f in d["funnel"]}
    assert funnel["new"] == 2 and funnel["meeting_booked"] == 1 and funnel["won"] == 0
    assert d["lead_to_meeting_rate"] == 0.5
    assert len(d["weekly_activity"]) == 8


def test_settings_reports_integrations_without_secrets(rep, admin, monkeypatch):
    info = rep.get("/api/settings/system").json()
    assert {i["key"] for i in info["integrations"]} >= {"email", "voice", "whatsapp"}
    assert all("value" not in i for i in info["integrations"])
    assert rep.delete("/api/settings/demo-data").status_code == 403
    assert admin.delete("/api/settings/demo-data").status_code == 200


def test_health(anon):
    assert anon.get("/api/health").json() == {"status": "ok"}
