import io

from tests.conftest import new_contact, new_institution


def test_email_duplicate_case_insensitive(rep):
    inst = new_institution(rep)
    new_contact(rep, inst["id"])
    r = rep.post(
        "/api/contacts", json={"institution_id": inst["id"], "first_name": "Someone", "email": "ASHA.RAO@test-uni.example"}
    )
    assert r.status_code == 409


def test_same_name_different_people_not_merged(rep):
    """Two people with the same name at different institutions, or the same name with different emails,
    are both kept. Same name at the same institution is allowed but flagged as a possible duplicate."""
    a = new_institution(rep)
    b = new_institution(rep, name="Other Uni", website="https://other.example")
    new_contact(rep, a["id"], email="asha1@test-uni.example")
    new_contact(rep, b["id"], email="asha@other.example")
    r = rep.post(
        "/api/contacts",
        json={"institution_id": a["id"], "first_name": "Asha", "last_name": "Rao", "email": "asha2@test-uni.example"},
    )
    assert r.status_code == 201
    assert rep.get("/api/contacts", params={"q": "asha"}).json()["total"] == 3
    dups = rep.get(
        "/api/contacts/duplicates", params={"first_name": "asha", "last_name": "RAO", "institution_id": a["id"]}
    ).json()
    assert len(dups) == 2 and all(not d["exact"] for d in dups)


def test_linkedin_normalised_and_unique(rep):
    inst = new_institution(rep)
    c = new_contact(rep, inst["id"], linkedin_url="https://www.linkedin.com/in/asha-rao/")
    assert c["linkedin_url"] == "https://linkedin.com/in/asha-rao"
    r = rep.post(
        "/api/contacts", json={"institution_id": inst["id"], "first_name": "B", "linkedin_url": "linkedin.com/in/Asha-Rao"}
    )
    assert r.status_code in (409, 422)
    r = rep.post(
        "/api/contacts", json={"institution_id": inst["id"], "first_name": "C", "linkedin_url": "https://evil.example/in/x"}
    )
    assert r.status_code == 422


def test_contact_validation_and_unknown_institution(rep):
    r = rep.post("/api/contacts", json={"institution_id": 999, "first_name": "X"})
    assert r.status_code == 404
    inst = new_institution(rep)
    r = rep.post("/api/contacts", json={"institution_id": inst["id"], "first_name": "", "email": "bad", "phone": "abc"})
    assert r.status_code == 422
    assert {e["field"] for e in r.json()["errors"]} >= {"first_name", "email", "phone"}


def test_contacts_listed_on_institution_and_filtered(rep):
    inst = new_institution(rep)
    new_contact(rep, inst["id"], verification_status="verified")
    new_contact(rep, inst["id"], first="Ben", last="Shah", email="ben@test-uni.example")
    r = rep.get("/api/contacts", params={"institution_id": inst["id"], "verification_status": "verified"}).json()
    assert r["total"] == 1
    assert rep.get(f"/api/institutions/{inst['id']}").json()["contact_count"] == 2


def test_contact_csv_import(rep):
    new_institution(rep)
    csv_text = (
        "institution_website,first_name,last_name,email,designation\n"
        "https://test-uni.example,Neha,Iyer,neha@test-uni.example,Registrar\n"
        "https://test-uni.example,Neha,Iyer,NEHA@test-uni.example,Registrar\n"
        "https://unknown.example,Ravi,K,ravi@unknown.example,Dean\n"
    )
    r = rep.post(
        "/api/contacts/import", params={"dry_run": "false"}, files={"file": ("c.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    ).json()
    assert (r["created"], r["duplicates"], r["errors"]) == (1, 1, 1)
