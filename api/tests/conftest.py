import os

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://plugdrop:plugdrop@localhost:5432/plugdrop_test"),
)
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")
os.environ["RESEARCH_WORKER"] = "off"  # tests drive the queue explicitly
os.environ["RESEARCH_REASONER"] = "rules"
os.environ.pop("ANTHROPIC_API_KEY", None)

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    assert "test" in str(engine.url.database), "Refusing to run tests against a non-test database"
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    yield
    names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def make_user(role: Role, email: str | None = None) -> User:
    with SessionLocal() as db:
        u = User(
            email=email or f"{role.value}@test.example",
            full_name=f"{role.value.title()} User",
            role=role.value,
            hashed_password=hash_password(PASSWORD),
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u


def login(user: User) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def rep() -> TestClient:
    return login(make_user(Role.rep))


@pytest.fixture
def manager() -> TestClient:
    return login(make_user(Role.manager))


@pytest.fixture
def admin() -> TestClient:
    return login(make_user(Role.admin))


@pytest.fixture
def anon() -> TestClient:
    return TestClient(app)


def new_institution(
    client: TestClient, name: str = "Test University", website: str | None = "https://test-uni.example", **kw: object
) -> dict:
    body = {"name": name, "website": website, "institution_type": "university", "country": "India", "city": "Pune", **kw}
    r = client.post("/api/institutions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def new_contact(
    client: TestClient,
    institution_id: int,
    first: str = "Asha",
    last: str = "Rao",
    email: str | None = "asha.rao@test-uni.example",
    **kw: object,
) -> dict:
    r = client.post(
        "/api/contacts", json={"institution_id": institution_id, "first_name": first, "last_name": last, "email": email, **kw}
    )
    assert r.status_code == 201, r.text
    return r.json()
