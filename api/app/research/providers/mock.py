"""Deterministic, fictional research provider for demos and tests.

It only produces data for institutions whose website is on the reserved ``.example`` domain
(the Stage 1 demo records), so it can never invent facts about a real organisation. For any
other institution it returns an empty result, which the scorer treats as missing information.

Every document, person and URL it returns is fictional. People never get LinkedIn URLs (a
fictional name on a real social network could point at a real person).
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta

from app.normalize import domain_from_website
from app.research.providers.base import PersonRecord, ProfileRecord, ProviderError, ProviderResult, SourceDocument

FIRST = [
    "Anika",
    "Rohit",
    "Lerato",
    "Marcus",
    "Sana",
    "Tobias",
    "Deepa",
    "Kofi",
    "Elena",
    "Imran",
    "Ngozi",
    "Victor",
    "Shreya",
    "Hamid",
    "Clara",
    "Yusuf",
    "Pooja",
    "Jonah",
    "Mei",
    "Arvind",
]
LAST = [
    "Desai",
    "Menon",
    "Otieno",
    "Fischer",
    "Qureshi",
    "Lindqvist",
    "Pillai",
    "Boateng",
    "Rossi",
    "Siddiqui",
    "Eze",
    "Lamarck",
    "Kulkarni",
    "Rahimi",
    "Moreau",
    "Aziz",
    "Bhatt",
    "Keller",
    "Tanaka",
    "Raghavan",
]

ROLE_TITLES = [
    ("Director of Admissions", "Admissions"),
    ("Head of Student Recruitment", "Admissions"),
    ("Chief Information Officer", "IT"),
    ("Head of Digital Transformation", "Strategy"),
    ("Contact Centre Manager", "Student Services"),
    ("Chief Operating Officer", "Leadership"),
    ("CRM Manager", "Marketing"),
    ("Chief Technology Officer", "IT"),
]

# (template, source_type, official, age_range_days or None for undated)
SIGNALS = [
    (
        "The admissions office handled more than {n},000 enquiries by phone, email and WhatsApp during the last intake.",
        "official_site",
        True,
        (20, 150),
        "Admissions report",
    ),
    (
        "{name} will open a new campus in {city2} for the {year} intake, with admissions run from the main campus.",
        "press_release",
        True,
        (15, 120),
        "New campus announcement",
    ),
    (
        "{name} launches a new {course} programme with applications opening next month.",
        "news",
        False,
        (10, 90),
        "New programme launch",
    ),
    (
        "Applications for the {month} {year} intake close on {day} {month}. Counsellors are available on the helpline.",
        "official_site",
        True,
        None,
        "Admissions deadlines",
    ),
    (
        "We are hiring {k} Admissions Counsellors to respond to a high volume of phone and WhatsApp enquiries.",
        "job_posting",
        False,
        (5, 60),
        "Job posting: Admissions Counsellor",
    ),
    (
        "We are recruiting a CRM Manager to lead the migration of student enquiries to a new CRM platform.",
        "job_posting",
        False,
        (10, 80),
        "Job posting: CRM Manager",
    ),
    (
        "{name} has issued a request for proposals for a contact centre telephony and CRM upgrade.",
        "news",
        False,
        (30, 200),
        "RFP notice",
    ),
    (
        "{name} announced a three-year digital transformation programme covering admissions and student services.",
        "press_release",
        True,
        (400, 700),
        "Digital transformation announcement",
    ),
    (
        "Students reported waiting several days for replies to admission enquiries during peak season.",
        "news",
        False,
        (60, 300),
        "Student forum coverage",
    ),
    (
        "{name} launched its {year} admissions campaign across digital channels and regional events.",
        "news",
        False,
        (5, 45),
        "Admissions campaign",
    ),
]

COURSES = ["MBA in Business Analytics", "B.Sc Nursing", "Data Science", "Cyber Security", "Hospitality Management"]
MONTHS = ["January", "July", "September"]


class MockProvider:
    name = "mock"
    _attempts: dict[str, int] = {}

    def research(self, *, name: str, website: str | None, country: str, city: str, roles: list[str]) -> ProviderResult:
        domain = domain_from_website(website) or ""
        if not domain.endswith(".example"):
            return ProviderResult(profile=None, calls=1)
        # Simulated transient outage for domains containing "flaky": fails on the first attempt only.
        if "flaky" in domain:
            n = self._attempts.get(domain, 0)
            self._attempts[domain] = n + 1
            if n == 0:
                raise ProviderError("Mock provider timeout (simulated)", transient=True)
        if "blocked" in domain:
            raise ProviderError("Mock provider: access not permitted for this source (simulated)", transient=False)

        rnd = random.Random(int(hashlib.sha256(domain.encode()).hexdigest()[:12], 16))
        now = datetime.now(UTC).replace(microsecond=0)
        base = f"https://www.{domain}"
        docs: list[SourceDocument] = []

        def doc(path: str, title: str, text: str, source_type: str, official: bool, published: datetime | None) -> SourceDocument:
            url = f"{base}{path}" if official else f"https://news.example/{domain.split('.')[0]}{path}"
            if source_type == "job_posting":
                url = f"https://jobs.example/{domain.split('.')[0]}{path}"
            d = SourceDocument(
                doc_id=f"D{len(docs) + 1}",
                url=url,
                title=title,
                text=text,
                source_type=source_type,
                retrieved_at=now,
                published_at=published,
                official=official,
            )
            docs.append(d)
            return d

        campus_count = rnd.choice([1, 1, 2, 3, 4, 6])
        about = doc(
            "/about",
            f"About {name}",
            f"{name} is a {('multi-campus ' if campus_count > 1 else '')}institution based in {city}, {country}"
            + (f", operating {campus_count} campuses." if campus_count > 1 else "."),
            "official_site",
            True,
            None,
        )
        profile = ProfileRecord(
            official_name=name,
            website=base,
            city=city,
            country=country,
            institution_type=None,
            campus_count=campus_count,
            student_size_range=None,
            source=about,
        )

        year = now.year + 1
        # Vary richness so demo prospects spread across tiers: some have few, older or weaker signals.
        richness = rnd.choice(["rich", "rich", "medium", "thin", "thin", "stale"])
        if richness == "stale":
            picks = [x for x in SIGNALS if x[3] and x[3][0] >= 60] + rnd.sample(SIGNALS, 1)
            picks = rnd.sample(picks, min(3, len(picks)))
        else:
            picks = rnd.sample(
                SIGNALS, {"rich": rnd.randint(5, 7), "medium": rnd.randint(3, 4), "thin": rnd.randint(1, 2)}[richness]
            )
        for i, (tpl, stype, official, age, title) in enumerate(picks):
            text = tpl.format(
                name=name,
                n=rnd.randint(8, 60),
                city2=rnd.choice(["Nairobi", "Pune", "Dubai", "Leeds", "Austin"]),
                year=year,
                course=rnd.choice(COURSES),
                month=rnd.choice(MONTHS),
                day=rnd.randint(10, 28),
                k=rnd.randint(3, 12),
            )
            published = now - timedelta(days=rnd.randint(*age)) if age else None
            doc(f"/{stype.replace('_', '-')}/{i + 1}", title, text, stype, official, published)

        chosen = []
        for title, dept in rnd.sample(ROLE_TITLES, rnd.randint(0 if richness in ("thin", "stale") else 1, 4)):
            fn, ln = rnd.choice(FIRST), rnd.choice(LAST)
            mode = rnd.choice(["published", "published", "provider_verified", "inferred", "none"])
            line = f"{fn} {ln}, {title}" + (f", {fn.lower()}.{ln.lower()}@{domain}" if mode == "published" else "")
            chosen.append((fn, ln, title, dept, mode, line))
        staff = doc(
            "/contact/leadership",
            f"{name} leadership and contacts",
            "Leadership team: " + "; ".join(c[5] for c in chosen) + ".",
            "official_site",
            True,
            None,
        )
        people = [
            PersonRecord(
                first_name=fn,
                last_name=ln,
                designation=title,
                department=dept,
                source=staff,
                excerpt=line,
                email=f"{fn.lower()}.{ln.lower()}@{domain}" if mode != "none" else None,
                email_source=mode if mode in ("published", "provider_verified") else None,
                email_inferred=mode == "inferred",
                provider_verified=mode == "provider_verified",
            )
            for fn, ln, title, dept, mode, line in chosen
        ]
        return ProviderResult(profile=profile, documents=docs, people=people, calls=3, cost_usd=0.0)
