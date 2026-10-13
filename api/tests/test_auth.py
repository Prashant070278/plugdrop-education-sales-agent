from app.models import Role
from tests.conftest import PASSWORD, make_user


def test_login_sets_httponly_cookie_and_me_works(anon):
    u = make_user(Role.rep)
    r = anon.post("/api/auth/login", json={"email": u.email.upper(), "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert anon.get("/api/auth/me").json()["email"] == u.email


def test_wrong_password_rejected(anon):
    u = make_user(Role.rep)
    r = anon.post("/api/auth/login", json={"email": u.email, "password": "nope"})
    assert r.status_code == 401


def test_endpoints_require_auth(anon):
    for path in ["/api/institutions", "/api/contacts", "/api/leads", "/api/dashboard", "/api/tasks"]:
        assert anon.get(path).status_code == 401, path


def test_inactive_user_cannot_use_session(admin, rep):
    me = rep.get("/api/auth/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"is_active": False}).status_code == 200
    assert rep.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie(rep):
    assert rep.post("/api/auth/logout").status_code == 204
    assert rep.get("/api/auth/me").status_code == 401


def test_user_management_is_admin_only(rep, manager, admin):
    body = {"email": "new@test.example", "full_name": "New", "role": "rep", "password": "long-enough-pw"}
    assert rep.post("/api/users", json=body).status_code == 403
    assert manager.post("/api/users", json=body).status_code == 403
    r = admin.post("/api/users", json=body)
    assert r.status_code == 201 and "password" not in r.json() and "hashed_password" not in r.json()
    assert admin.post("/api/users", json=body).status_code == 409


def test_admin_cannot_demote_self(admin):
    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/users/{me['id']}", json={"role": "rep"}).status_code == 422
