from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Contact, Institution, Lead
from app.seed import purge_demo, seed_demo, seed_users


def test_seed_is_all_demo_and_fictional():
    with SessionLocal() as db:
        users = seed_users(db)
        seed_demo(db, users)
        db.commit()
        insts = db.scalars(select(Institution)).all()
        assert insts and all(i.is_demo for i in insts)
        assert all(i.domain and i.domain.endswith(".example") for i in insts)
        assert all((c.email or "").endswith(".example") for c in db.scalars(select(Contact)))
        stages = {s for (s,) in db.execute(select(Lead.stage).distinct())}
        assert {"new", "qualified", "meeting_booked", "won", "lost", "nurture"} <= stages
        purge_demo(db)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Institution)) == 0
