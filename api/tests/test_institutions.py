import io

from tests.conftest import new_contact, new_institution


def test_create_and_detail(rep):
    inst = new_institution(rep, campuses=[{"name": "Main Campus", "city": "Pune"}], courses=["MBA", " MBA ", "BBA"])
    assert inst["domain"] == "test-uni.example"
    assert inst["courses"] == ["MBA", "BBA"]
    assert inst["campus_count"] == 1 and len(inst["campuses"]) == 1
    assert inst["freshness"] == "never"
    assert inst["owner"]["full_name"] == "Rep User"


def test_duplicate_by_domain_blocked(rep):
    new_institution(rep)
    r = rep.post("/api/institutions", json={"name": "Totally Different", "website": "http://www.test-uni.example/about"})
    assert r.status_code == 409
    assert r.json()["detail"]["duplicates"][0]["reason"].startswith("Same website domain")


def test_duplicate_by_name_and_location_blocked_but_other_city_allowed(rep):
    new_institution(rep, name="The Royal College", website=None)
    r = rep.post("/api/institutions", json={"name": "Royal College", "country": "India", "city": "Pune"})
    assert r.status_code == 409
    r = rep.post("/api/institutions", json={"name": "Royal College", "country": "India", "city": "Delhi"})
    assert r.status_code == 201
    dups = rep.get("/api/institutions/duplicates", params={"name": "Royal College", "country": "India", "city": "Goa"}).json()
    assert len(dups) == 2 and not any(d["exact"] for d in dups)


def test_validation_errors(rep):
    r = rep.post("/api/institutions", json={"name": "X", "website": "not a url", "institution_type": "spaceship"})
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert {"name", "website", "institution_type"} <= fields


def test_list_search_filter_sort_paginate(rep):
    for i, (name, t, country) in enumerate(
        [("Alpha University", "university", "India"), ("Beta EdTech", "edtech", "Kenya"), ("Gamma College", "college", "India")]
    ):
        new_institution(rep, name=name, website=f"https://s{i}.example", institution_type=t, country=country)
    r = rep.get("/api/institutions", params={"q": "beta"}).json()
    assert r["total"] == 1 and r["items"][0]["name"] == "Beta EdTech"
    r = rep.get("/api/institutions", params={"country": "india", "sort": "-name"}).json()
    assert [i["name"] for i in r["items"]] == ["Gamma College", "Alpha University"]
    r = rep.get("/api/institutions", params={"institution_type": ["edtech", "college"]}).json()
    assert r["total"] == 2
    r = rep.get("/api/institutions", params={"page": 2, "page_size": 2}).json()
    assert r["total"] == 3 and len(r["items"]) == 1
    assert rep.get("/api/institutions", params={"sort": "hashed_password"}).status_code == 422


def test_update_and_audit_history(rep):
    inst = new_institution(rep)
    r = rep.patch(f"/api/institutions/{inst['id']}", json={"city": "Mumbai", "student_size_range": "5k_20k"})
    assert r.status_code == 200 and r.json()["city"] == "Mumbai"
    hist = rep.get(f"/api/audit/entity/institution/{inst['id']}").json()
    assert hist[0]["action"] == "update"
    assert hist[0]["changes"]["city"] == {"from": "Pune", "to": "Mumbai"}
    assert hist[-1]["action"] == "create"


def test_delete_requires_manager_and_no_contacts(rep, manager):
    inst = new_institution(rep)
    new_contact(rep, inst["id"])
    assert rep.delete(f"/api/institutions/{inst['id']}").status_code == 403
    assert manager.delete(f"/api/institutions/{inst['id']}").status_code == 409
    other = new_institution(rep, name="Empty Uni", website="https://empty.example")
    assert manager.delete(f"/api/institutions/{other['id']}").status_code == 204


def test_campus_unique_per_institution(rep):
    inst = new_institution(rep)
    assert rep.post(f"/api/institutions/{inst['id']}/campuses", json={"name": "North"}).status_code == 201
    assert rep.post(f"/api/institutions/{inst['id']}/campuses", json={"name": "North"}).status_code == 409


def test_csv_import_dry_run_then_commit_and_export(rep):
    new_institution(rep, name="Existing Uni", website="https://existing.example")
    csv_text = (
        "name,website,institution_type,country,city,courses\n"
        "Fresh University,https://fresh.example,university,India,Pune,MBA;BBA\n"
        "Existing Dup,https://www.existing.example,college,India,Pune,\n"
        "Fresh Copy,https://fresh.example,college,India,Pune,\n"
        ",https://noname.example,college,India,Pune,\n"
        "Bad Type,https://bad.example,spaceship,India,Pune,\n"
    )
    files = {"file": ("inst.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    r = rep.post("/api/institutions/import", files=files, params={"dry_run": "true"}).json()
    assert (r["created"], r["duplicates"], r["errors"]) == (1, 2, 2)
    assert rep.get("/api/institutions").json()["total"] == 1  # dry run wrote nothing
    files = {"file": ("inst.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    r = rep.post("/api/institutions/import", files=files, params={"dry_run": "false"}).json()
    assert r["created"] == 1
    assert rep.get("/api/institutions").json()["total"] == 2
    out = rep.get("/api/institutions/export.csv")
    assert out.status_code == 200 and out.headers["content-type"].startswith("text/csv")
    lines = out.text.strip().splitlines()
    assert len(lines) == 3 and "Fresh University" in out.text and "MBA; BBA" in out.text


def test_export_neutralises_spreadsheet_formulas(rep):
    new_institution(rep, name="=HYPERLINK(evil)", website="https://formula.example")
    out = rep.get("/api/institutions/export.csv").text
    assert "'=HYPERLINK(evil)" in out


def test_rep_cannot_read_full_audit_log(rep, manager):
    assert rep.get("/api/audit").status_code == 403
    assert manager.get("/api/audit").status_code == 200
