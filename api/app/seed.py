"""Seed users and clearly-labelled fictional demo data.

All demo organisations and people are invented. Websites use the reserved `.example`
top-level domain so no real institution can be contacted. Every demo row has
``is_demo = true`` and source "DEMO DATA (fictional)".

Usage:
    python -m app.seed              # users + demo data (skips demo data if already present)
    python -m app.seed --reset      # delete existing demo rows first
    python -m app.seed --users-only # only the three login users
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import (
    FUNNEL,
    Activity,
    Campaign,
    Campus,
    ConsentRecord,
    Contact,
    Institution,
    Lead,
    Opportunity,
    ResearchFinding,
    Role,
    Task,
    User,
)
from app.normalize import domain_from_website, normalize_name
from app.security import hash_password

SOURCE = "DEMO DATA (fictional)"

USERS = [
    ("admin@plugdrop.example", "Asha Admin", Role.admin),
    ("manager@plugdrop.example", "Mohan Manager", Role.manager),
    ("rep@plugdrop.example", "Riya Rep", Role.rep),
]

INSTITUTIONS = [
    ("Northbridge University", "university", "India", "Pune", "20k_50k", ["B.Tech", "MBA", "B.Com", "M.Sc Data Science"]),
    ("Lakeshore Institute of Technology", "college", "India", "Bengaluru", "5k_20k", ["B.Tech", "M.Tech", "BCA"]),
    (
        "Crescent Valley University Group",
        "university_group",
        "United Arab Emirates",
        "Dubai",
        "over_50k",
        ["MBA", "BBA", "Engineering", "Medicine"],
    ),
    ("Brightpath Learning", "edtech", "India", "Gurugram", "over_50k", ["Coding Bootcamp", "Data Analytics", "UX Design"]),
    ("Summit Test Prep Academy", "test_prep", "India", "Delhi", "5k_20k", ["JEE", "NEET", "CAT"]),
    ("Meridian College of Commerce", "college", "India", "Mumbai", "1k_5k", ["B.Com", "BBA", "CA Foundation"]),
    (
        "Harbourline Online University",
        "online_education",
        "United Kingdom",
        "Manchester",
        "20k_50k",
        ["Online MBA", "MSc Computing", "BA Business"],
    ),
    ("Savanna Polytechnic", "vocational", "Kenya", "Nairobi", "5k_20k", ["Electrical Engineering Diploma", "Hospitality", "ICT"]),
    (
        "Kestrel School of Professional Studies",
        "professional_education",
        "Singapore",
        "Singapore",
        "1k_5k",
        ["CFA Prep", "Project Management", "HR Certification"],
    ),
    ("Riverbend University", "university", "United States", "Austin", "20k_50k", ["BS Computer Science", "BBA", "Nursing"]),
    ("Orchid Valley Medical College", "college", "India", "Hyderabad", "1k_5k", ["MBBS", "B.Sc Nursing", "BPT"]),
    ("Aurora Skills Institute", "vocational", "India", "Chennai", "1k_5k", ["Automotive Technician", "Welding", "Electrician"]),
    ("Granite Peak Law School", "professional_education", "India", "Bengaluru", "under_1k", ["LLB", "LLM", "BA LLB"]),
    ("Coastal Business School", "college", "United Arab Emirates", "Abu Dhabi", "1k_5k", ["MBA", "Executive MBA"]),
    ("Pinewood International University", "university", "Kenya", "Mombasa", "5k_20k", ["BBA", "BSc IT", "Education"]),
    ("Quillstone EdTech", "edtech", "United Kingdom", "London", "over_50k", ["GCSE Tutoring", "A-Level Prep"]),
    ("Starling Language Academy", "online_education", "Singapore", "Singapore", "5k_20k", ["IELTS", "Business English"]),
    ("Evergreen Design University", "university", "India", "Ahmedabad", "5k_20k", ["B.Des", "M.Des", "Animation"]),
    (
        "Tidewater Community College Network",
        "university_group",
        "United States",
        "Norfolk",
        "over_50k",
        ["Associate Degrees", "Nursing", "IT"],
    ),
    ("Zenith Civil Services Coaching", "test_prep", "India", "Jaipur", "1k_5k", ["UPSC", "State PSC"]),
    ("Larkspur Hospitality Institute", "vocational", "India", "Goa", "under_1k", ["Hotel Management", "Culinary Arts"]),
    ("Silverline Online Academy", "online_education", "India", "Kolkata", "20k_50k", ["Online BBA", "Online MCA"]),
]

FIRST = [
    "Aarav",
    "Meera",
    "Daniel",
    "Fatima",
    "Kwame",
    "Priya",
    "Liam",
    "Sofia",
    "Arjun",
    "Nadia",
    "Ethan",
    "Zara",
    "Rahul",
    "Amina",
    "Oliver",
    "Ishita",
    "Samuel",
    "Leila",
    "Vikram",
    "Grace",
    "Rohan",
    "Hana",
    "Kabir",
    "Wanjiru",
]
LAST = [
    "Mehta",
    "Iyer",
    "Okafor",
    "Shah",
    "Mensah",
    "Reddy",
    "Carter",
    "Haddad",
    "Kapoor",
    "Njoroge",
    "Thompson",
    "Rao",
    "Banerjee",
    "Odhiambo",
    "Fernandes",
    "Khan",
    "Nair",
    "Walker",
    "Joshi",
    "Ali",
]
ROLES = [
    ("Director of Admissions", "Admissions"),
    ("Head of Student Recruitment", "Admissions"),
    ("Chief Information Officer", "IT"),
    ("Registrar", "Registrar's Office"),
    ("VP Marketing", "Marketing"),
    ("Head of Student Support", "Student Services"),
    ("Dean of Academics", "Academics"),
    ("CEO", "Leadership"),
]
FINDINGS = [
    (
        "Admissions volume",
        "Admissions team handles a large spike of enquiries each intake; site shows a phone-only enquiry line.",
    ),
    ("Tech stack", "Careers page mentions a CRM migration project this year."),
    ("Pain point", "Public reviews mention slow responses to admission queries."),
    ("Expansion", "Announced a new campus opening next academic year."),
    ("Channels", "Runs WhatsApp-based counselling for international applicants."),
]


def slug(name: str) -> str:
    return normalize_name(name).replace(" ", "-")


def seed_users(db: Session) -> dict[Role, User]:
    password = os.environ.get("SEED_PASSWORD")
    if not password:
        if os.environ.get("ENVIRONMENT", "development") == "production":
            raise SystemExit("Set SEED_PASSWORD to seed users in production")
        password = "PlugDrop-demo-2026"
    out: dict[Role, User] = {}
    for email, name, role in USERS:
        u = db.scalar(select(User).where(func.lower(User.email) == email))
        if u is None:
            u = User(email=email, full_name=name, role=role.value, hashed_password=hash_password(password))
            db.add(u)
        out[role] = u
    db.flush()
    return out


def purge_demo(db: Session) -> None:
    for m in (Activity, ResearchFinding, Task, ConsentRecord, Opportunity, Lead, Contact, Campaign, Institution):
        db.execute(delete(m).where(m.is_demo.is_(True)))
    db.flush()


def seed_demo(db: Session, users: dict[Role, User]) -> None:
    rnd = random.Random(42)
    now = datetime.now(UTC)
    reps = [users[Role.rep], users[Role.manager]]

    campaigns = [
        Campaign(
            name="DEMO · Admissions Season Voice AI",
            channel="multi",
            status="active",
            description="Fictional demo campaign targeting admissions leaders before the next intake.",
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=60),
            owner_id=users[Role.manager].id,
            is_demo=True,
        ),
        Campaign(
            name="DEMO · EdTech Support Automation",
            channel="email",
            status="draft",
            description="Fictional demo campaign for EdTech student-support teams.",
            owner_id=users[Role.manager].id,
            is_demo=True,
        ),
    ]
    db.add_all(campaigns)
    db.flush()

    insts: list[Institution] = []
    for i, (name, itype, country, city, size, courses) in enumerate(INSTITUTIONS):
        website = f"https://www.{slug(name)}.example"
        inst = Institution(
            name=name,
            normalized_name=normalize_name(name),
            website=website,
            domain=domain_from_website(website),
            institution_type=itype,
            country=country,
            city=city,
            student_size_range=size,
            courses=courses,
            source=SOURCE,
            owner_id=reps[i % 2].id,
            is_demo=True,
            last_verified_at=now - timedelta(days=rnd.choice([5, 20, 45, 100, 150, 220])) if i % 7 else None,
            notes="Fictional institution created for demonstration.",
        )
        n_campus = rnd.choice([0, 1, 2, 3]) if itype in ("university", "university_group", "college") else 0
        inst.campuses = [
            Campus(name=f"{city} {['Main', 'North', 'City', 'South'][k]} Campus", city=city, country=country)
            for k in range(n_campus)
        ]
        insts.append(inst)
    db.add_all(insts)
    db.flush()

    contacts_by_inst: dict[int, list[Contact]] = {}
    used: set[tuple[str, str]] = set()
    for inst in insts:
        n = rnd.randint(2, 4)
        roles = rnd.sample(ROLES, n)
        lst = []
        for title, dept in roles:
            while True:
                fn, ln = rnd.choice(FIRST), rnd.choice(LAST)
                if (fn, ln) not in used:
                    used.add((fn, ln))
                    break
            c = Contact(
                institution_id=inst.id,
                first_name=fn,
                last_name=ln,
                designation=title,
                department=dept,
                email=f"{fn.lower()}.{ln.lower()}@{inst.domain}",
                phone=f"+00 {rnd.randint(100, 999)} {rnd.randint(1000, 9999)}",
                verification_status=rnd.choice(["verified", "verified", "unverified", "bounced"]),
                source=SOURCE,
                is_demo=True,
                last_verified_at=now - timedelta(days=rnd.choice([3, 30, 95, 200])) if rnd.random() > 0.2 else None,
            )
            lst.append(c)
        contacts_by_inst[inst.id] = lst
        db.add_all(lst)
    db.flush()

    stage_plan = [
        "new",
        "new",
        "researched",
        "researched",
        "qualified",
        "qualified",
        "outreach_ready",
        "contacted",
        "contacted",
        "engaged",
        "engaged",
        "meeting_booked",
        "meeting_booked",
        "proposal",
        "proposal",
        "negotiation",
        "won",
        "lost",
        "nurture",
    ]
    funnel_vals = [s.value for s in FUNNEL]
    leads: list[Lead] = []
    for inst, stage in zip(insts, stage_plan, strict=False):
        contact = contacts_by_inst[inst.id][0]
        days_ago = rnd.randint(10, 80)
        lead = Lead(
            institution_id=inst.id,
            primary_contact_id=contact.id,
            owner_id=inst.owner_id,
            stage=stage,
            score=rnd.randint(20, 95),
            source=SOURCE,
            is_demo=True,
            campaign_id=campaigns[0].id if rnd.random() > 0.4 else None,
            summary=f"Fictional lead: {inst.name} is evaluating automation for admissions enquiries.",
            stage_changed_at=now - timedelta(days=rnd.randint(1, 9)),
            lost_reason="Chose to build in-house (demo)" if stage == "lost" else None,
        )
        db.add(lead)
        db.flush()
        leads.append(lead)
        # Stage history so the funnel and timeline are realistic.
        path = (
            funnel_vals[: funnel_vals.index(stage) + 1]
            if stage in funnel_vals
            else ["new", "researched", "qualified", "contacted", stage]
        )
        t = now - timedelta(days=days_ago)
        prev = None
        for s in path:
            db.add(
                Activity(
                    activity_type="status_change",
                    subject=(
                        f"Stage changed: {prev.replace('_', ' ')} → {s.replace('_', ' ')}"
                        if prev
                        else f"Lead created at stage {s}"
                    ),
                    occurred_at=t,
                    lead_id=lead.id,
                    institution_id=inst.id,
                    user_id=lead.owner_id,
                    details={"from": prev, "to": s},
                    is_demo=True,
                )
            )
            prev = s
            t += timedelta(days=rnd.randint(1, 5))
        if stage != "new":
            cat, summary = rnd.choice(FINDINGS)
            db.add(
                ResearchFinding(
                    institution_id=inst.id,
                    lead_id=lead.id,
                    category=cat,
                    summary=summary + " (demo)",
                    source_url=f"{inst.website}/about",
                    confidence=rnd.randint(40, 90),
                    user_id=lead.owner_id,
                    is_demo=True,
                )
            )
            db.add(
                Activity(
                    activity_type="research",
                    subject=f"Research: {cat}",
                    body=summary + " (demo)",
                    occurred_at=now - timedelta(days=days_ago - 1),
                    lead_id=lead.id,
                    institution_id=inst.id,
                    user_id=lead.owner_id,
                    is_demo=True,
                )
            )
        reached = funnel_vals.index(stage) if stage in funnel_vals else 4
        if reached >= funnel_vals.index("contacted"):
            for _ in range(rnd.randint(1, 4)):
                typ = rnd.choice(["call", "email", "email"])
                db.add(
                    Activity(
                        activity_type=typ,
                        subject=("Intro call with " if typ == "call" else "Intro email to ") + contact.full_name,
                        body="Discussed admissions enquiry volumes and response times (demo).",
                        occurred_at=now - timedelta(days=rnd.randint(0, 40)),
                        lead_id=lead.id,
                        institution_id=inst.id,
                        contact_id=contact.id,
                        user_id=lead.owner_id,
                        is_demo=True,
                    )
                )
        if reached >= funnel_vals.index("engaged"):
            db.add(
                Activity(
                    activity_type="reply",
                    subject=f"Reply from {contact.full_name}",
                    body="Interested — asked for pricing for 3 campuses (demo).",
                    occurred_at=now - timedelta(days=rnd.randint(0, 20)),
                    lead_id=lead.id,
                    institution_id=inst.id,
                    contact_id=contact.id,
                    user_id=lead.owner_id,
                    is_demo=True,
                )
            )
        if reached >= funnel_vals.index("meeting_booked"):
            db.add(
                Activity(
                    activity_type="meeting",
                    subject="Discovery meeting: admissions Voice AI",
                    body="Walked through Voice AI counselling flow and CRM sync (demo).",
                    occurred_at=now - timedelta(days=rnd.randint(0, 25)),
                    lead_id=lead.id,
                    institution_id=inst.id,
                    contact_id=contact.id,
                    user_id=lead.owner_id,
                    is_demo=True,
                )
            )
        if reached >= funnel_vals.index("qualified") and stage != "lost":
            opp_stage = {"proposal": "proposal", "negotiation": "negotiation", "won": "won"}.get(
                stage, "demo" if reached >= funnel_vals.index("meeting_booked") else "discovery"
            )
            opp = Opportunity(
                lead_id=lead.id,
                institution_id=inst.id,
                name=f"{inst.name} — Admissions Voice AI + CRM",
                estimated_value=Decimal(rnd.choice([8000, 12000, 18000, 25000, 40000, 60000])),
                currency="USD",
                solution_interest=rnd.sample(["voice_ai", "crm", "chat", "whatsapp", "automation"], 2),
                expected_decision_date=date.today() + timedelta(days=rnd.randint(10, 120)),
                owner_id=lead.owner_id,
                stage=opp_stage,
                next_action="Send tailored proposal (demo)",
                next_action_date=date.today() + timedelta(days=rnd.randint(-3, 14)),
                is_demo=True,
            )
            db.add(opp)
        if stage not in ("won", "lost"):
            due = now + timedelta(days=rnd.randint(-4, 10), hours=rnd.randint(0, 8))
            db.add(
                Task(
                    title=rnd.choice(
                        [
                            "Follow up on pricing question",
                            "Book discovery call",
                            "Verify admissions director email",
                            "Send case study",
                            "Prepare proposal draft",
                        ]
                    )
                    + " (demo)",
                    due_at=due,
                    reminder_at=due - timedelta(hours=2),
                    priority=rnd.choice(["low", "medium", "high", "urgent"]),
                    owner_id=lead.owner_id,
                    lead_id=lead.id,
                    institution_id=inst.id,
                    contact_id=contact.id,
                    is_demo=True,
                )
            )
    # One completed task and a suppression example.
    db.add(
        Task(
            title="Clean up imported contact list (demo)",
            status="done",
            completed_at=now - timedelta(days=2),
            due_at=now - timedelta(days=3),
            priority="low",
            owner_id=users[Role.rep].id,
            is_demo=True,
        )
    )
    opted_out = contacts_by_inst[insts[-1].id][0]
    db.add(
        ConsentRecord(
            contact_id=opted_out.id,
            email=opted_out.email,
            channel="all",
            status="opted_out",
            reason="Asked not to be contacted (demo)",
            source=SOURCE,
            recorded_by_id=users[Role.rep].id,
            is_demo=True,
        )
    )
    db.add(
        ConsentRecord(
            domain="www.larkspur-hospitality-institute.example".removeprefix("www."),
            channel="email",
            status="suppressed",
            reason="Institution-wide email suppression (demo)",
            source=SOURCE,
            recorded_by_id=users[Role.manager].id,
            is_demo=True,
        )
    )
    db.flush()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true", help="delete existing demo rows first")
    p.add_argument("--users-only", action="store_true")
    a = p.parse_args()
    with SessionLocal() as db:
        users = seed_users(db)
        if not a.users_only:
            if a.reset:
                purge_demo(db)
            if db.scalar(select(func.count()).select_from(Institution).where(Institution.is_demo.is_(True))):
                print("Demo data already present; use --reset to recreate it.")
            else:
                seed_demo(db, users)
                print("Demo data created.")
        db.commit()
    print("Users: " + ", ".join(e for e, _, _ in USERS))


if __name__ == "__main__":
    main()
