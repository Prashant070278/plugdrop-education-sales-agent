# PlugDrop.AI Sales CRM (internal)

The PlugDrop.AI sales team uses this CRM to sell Voice AI + CRM solutions to universities, colleges, university groups, EdTech companies, test-prep businesses and vocational/professional education providers.

**Stage 1** is the foundation: a CRM backed by a database, with institutions, contacts, a 12-stage lead pipeline, opportunities, activity timelines, tasks, CSV import/export, duplicate detection, opt-out/suppression and a full audit history.

| Layer | Stack |
|---|---|
| Web | Next.js 15 (App Router) · TypeScript · Tailwind CSS · shadcn/ui components (Radix) |
| API | Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic v2 |
| Database | PostgreSQL 16 |
| Auth | Email + password (bcrypt). Signed JWT in an HttpOnly, SameSite=Lax cookie. Roles: admin / manager / rep |
| Dev | Docker Compose, Makefile, GitHub Actions CI |

The browser only ever talks to the Next.js server. Next.js proxies `/api/*` to FastAPI, so the session cookie stays first-party.

---

## Quick start (Docker)

```bash
cp .env.example .env                 # then set POSTGRES_PASSWORD, JWT_SECRET, SEED_PASSWORD
docker compose up --build -d         # Postgres + API (runs migrations on start) + web
docker compose exec api python -m app.seed   # 3 users + fictional demo data
open http://localhost:3000
```

Sign in with `admin@plugdrop.example`, `manager@plugdrop.example` or `rep@plugdrop.example`. In development the password is the value of `SEED_PASSWORD` (default `PlugDrop-demo-2026` if unset). Change these users, or deactivate them, before real use.

API docs (OpenAPI/Swagger): http://localhost:8000/api/docs

## Local development without Docker

Requirements: Python 3.12+, Node 22+, PostgreSQL 16.

```bash
# database
createuser -P plugdrop            # password e.g. plugdrop
createdb -O plugdrop plugdrop
createdb -O plugdrop plugdrop_test

# API
cd api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://plugdrop:plugdrop@localhost:5432/plugdrop
export JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000

# web (new terminal)
cd web
npm install
npm run dev                         # http://localhost:3000, proxies /api to :8000
```

Set `API_INTERNAL_URL` if the API is not at `http://localhost:8000`. Next.js reads it at build time to configure the proxy.

## Environment variables

All secrets come from the environment. `.env` is git-ignored, and `.env.example` lists every variable.

| Variable | Used by | Notes |
|---|---|---|
| `DATABASE_URL` | API | `postgresql+psycopg://user:pass@host:5432/db` |
| `JWT_SECRET` | API | **Required in production**: the API refuses to start with the default. Use 48+ random bytes. |
| `ENVIRONMENT` | API, seed | `development` or `production` |
| `ACCESS_TOKEN_MINUTES` | API | Session length (default 480) |
| `COOKIE_SECURE` | API | `true` when served over HTTPS |
| `CORS_ORIGINS` | API | Comma-separated. Only needed if the browser calls the API directly. |
| `SEED_PASSWORD` | seed | Password for the 3 seeded users. Required in production. |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `VOICE_AI_API_KEY`, `WHATSAPP_API_TOKEN`, `CALENDAR_API_KEY` | API | Optional. Settings → Integrations shows only whether each one is configured, never its value. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose | Database container |
| `API_INTERNAL_URL` | web (build) | Where Next.js proxies `/api` |

## Database

**Migrations.** Alembic migrations are in `api/alembic/versions`. Run `alembic upgrade head` to apply them (the API container does this on start) and `alembic downgrade -1` to roll back. After changing `app/models.py`, run `alembic revision --autogenerate -m "…"` and review the generated file.

**Tables.** `users`, `institutions`, `campuses`, `contacts`, `leads`, `opportunities`, `activities`, `research_findings`, `tasks`, `campaigns`, `consent_records`, `audit_events`. Every table has foreign keys, indexes on its lookup and sort columns, CHECK constraints for enumerations and ranges, and `created_at`/`updated_at`.

**Duplicate rules** (enforced in the database and checked by the API before saving):

| Record | Blocked as duplicate | Flagged for review only |
|---|---|---|
| Institution | Same website domain (`www.` and paths ignored), **or** same normalised name in the same city and country | Same name in a different location |
| Contact | Same email (case-insensitive) **or** same LinkedIn profile URL | Same first + last name at the same institution |
| Lead | A second *open* lead for an institution (won/lost leads may accumulate) | — |
| Campus | Same campus name within one institution | — |

People with similar or identical names are **never merged automatically**. A contact is only blocked when an exact identifier matches.

**Demo data.** `python -m app.seed` creates 22 fictional institutions, about 70 contacts, leads across every stage, opportunities, activity history, research findings, tasks, 2 campaigns and 2 suppression records. Everything is fictional and marked:

- `is_demo = true` in every table, shown as a **DEMO** badge in the UI
- websites and emails use the reserved `.example` domain, so nothing real can be contacted
- source is "DEMO DATA (fictional)"

`python -m app.seed --reset` recreates the demo data. Admins can also delete it all from **Settings → Demo data**. Real records are never touched.

## What's in Stage 1

| Area | What works |
|---|---|
| **Dashboard** | Total prospects, qualified leads, outreach activity (calls + emails), meetings, open pipeline value, won value, lead → meeting rate, win rate, tasks due/overdue. Shows a conversion funnel (based on stage history, so leads lost later still count) and weekly outreach for 8 weeks. Toggles between team and mine and between 7/30/90/365 days. |
| **Institutions** | List with search, filters (type, country, size, demo/real), sorting, pagination, CSV export of the filtered set and CSV import (check first, then confirm). Duplicate warnings while you type. The detail page shows linked contacts, campuses, leads, an activity timeline, research findings and an audit trail. |
| **Contacts** | List with search, filters, sort, CSV import/export, verification status, data freshness, and a "do not contact" flag. |
| **Lead pipeline** | All 12 stages: New, Researched, Qualified, Outreach Ready, Contacted, Engaged, Meeting Booked, Proposal, Negotiation, Won, Lost, Nurture. Board and table views. Every stage change is written to the timeline and the audit log. Lost leads need a reason. Outreach stages need a primary contact who isn't suppressed. |
| **Lead detail** | Stage track, chronological timeline (status changes, calls, emails, replies, meetings, research, notes), tasks, opportunities, research and audit. Log activity, add tasks and move stage from the page. |
| **Opportunities** | Board (with total value per stage) and table. Each has estimated value, currency, solution interest (Voice AI, CRM, chat, WhatsApp, automation), expected decision date, owner, stage and next action/date. |
| **Tasks & reminders** | Due date, reminder (must be before the due date), priority, owner and completion. Filter by mine, overdue, today or this week. |
| **Opt-out / suppression** | Record by contact, email or whole domain. Suppressed contacts can't be moved into outreach stages or receive call/email activities. |
| **Settings** | Integration status, users and roles (admin), opt-outs, campaigns, the full audit log (manager+) and demo-data removal (admin). |
| **Data freshness** | `last_verified_at` on institutions and contacts, shown as Fresh (under 90 days), Aging (under 180 days), Stale or Unverified. Tick "I have verified these details today" when saving. |
| **Audit** | Every create, update, delete, import, stage change, login and purge is stored with the user, timestamp, IP and a before/after diff of the changed fields. |

**Permissions**

| | Rep | Manager | Admin |
|---|---|---|---|
| View and edit institutions, contacts, leads, opportunities, activities | ✓ | ✓ | ✓ |
| Record an opt-out | ✓ | ✓ | ✓ |
| Edit or delete other people's tasks | | ✓ | ✓ |
| Delete institutions, contacts, leads or opportunities; remove opt-outs; record consent; create campaigns; view the full audit log | | ✓ | ✓ |
| Manage users; delete demo data | | | ✓ |

## CSV formats

**Institutions:** `name` (required), `website`, `institution_type`, `country`, `city`, `student_size_range`, `courses` (separated by `;`), `source`.

**Contacts:** `first_name` (required), plus `institution_website` or `institution` (name) to link the contact. Optional: `last_name`, `designation`, `department`, `email`, `phone`, `linkedin_url`, `verification_status`, `source`.

Import limits: UTF-8, 5 MB, 5,000 rows. Every import is a dry run first and reports, per row, what would be created, which rows are duplicates and which have errors. Exported cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so spreadsheets can't run them as formulas.

## Tests and checks

```bash
cd api
pytest                 # 69 tests against a real Postgres (plugdrop_test; override with TEST_DATABASE_URL)
ruff check app tests
mypy app

cd ../web
npm run lint
npm run typecheck
npm run build
```

The test suite drops and recreates the schema in the test database through the Alembic migrations. It refuses to run against a database whose name doesn't contain `test`. The tests cover authentication and roles, validation, duplicate rules (including same-name people who must not be merged), pipeline rules, suppression (by contact and by domain), opportunities, tasks, dashboard maths, CSV import/export, audit history and the seed script.

GitHub Actions (`.github/workflows/ci.yml`) runs everything above on every push and pull request.

## How to verify Stage 1 by hand

1. Run `docker compose up --build -d && docker compose exec api python -m app.seed` and sign in as `manager@plugdrop.example`.
2. **Dashboard:** the numbers should match the demo data (22 prospects). Switch between Team and Mine.
3. **Institutions → New institution:** enter website `https://aurora-skills-institute.example`. You get a duplicate warning, and saving is blocked. Change the website and save.
4. On the new institution, add a contact, then **Create lead** with that contact. **Move stage** to *Contacted* and check that it appears on the timeline.
5. Back on the institution, click the shield icon on the contact to record an opt-out. Then try moving the lead to *Engaged*: it's refused with "opted out or suppressed".
6. **Contacts → Import CSV:** upload a file with one new row, one duplicate email and one unknown institution. The check reports 1 / 1 / 1.
7. Sign in as `rep@plugdrop.example`. There's no delete button on institutions, and Settings has no Users or Audit log tabs.

## Stage 2: AI prospect research agent

The agent researches an institution from **cited sources**, proposes findings and decision-makers, and scores the prospect out of 100 with an explanation for every point. A person reviews the result, and only approved items go into the CRM. No outreach is sent.

**How it works**

1. **Queue.** On **Institutions**, tick one or more rows and choose **Research selected**. Or open an institution and click **Research now**. Each institution becomes one *run* inside a *job*.
2. **Retrieve.** A worker takes the run and asks the configured **research source provider** for the official profile, source documents (official pages, press releases, job postings, news) and people. Providers are swappable (`app/research/providers/`). The built-in `mock` provider returns fictional data, and only for `.example` demo institutions.
3. **Reason.** Claude (when `ANTHROPIC_API_KEY` is set) or a rule-based reasoner reads **only the retrieved documents** and classifies each finding:
   - **Verified fact:** stated directly in the source. The quoted excerpt is checked against the source text, and a quote that isn't there is downgraded.
   - **Source-supported inference:** a reasonable inference from a cited source. News and forums count here unless the institution itself says it.
   - **Hypothesis:** no supporting source. Shown for follow-up, never scored, and can't be approved.
4. **Score.** Deterministic, with no AI involved. Each part is weighted (configurable): Institution fit 25, Business problem 25, Intent/timing 20, Decision-maker access 15, Evidence quality and freshness 15. Missing information scores zero. Inferences count less than verified facts. Evidence loses value with age (full value up to 180 days, discounted after, heavily after 365) and when its confidence is weak. Tiers: A ≥ 75, B ≥ 55, C ≥ 35, D below 35.
5. **Review.** **Research → Review** shows the score with reasons, why the institution fits, the most relevant use case, the recommended next action, missing information and how to verify it, decision-makers with their email status, and every piece of evidence with its source link, quote, publication date and retrieval date. Approve or reject each item.
6. **Push.** **Push to CRM** writes the approved evidence as research findings (keeping the evidence level, quote and dates) and the approved people as contacts. It links people already in the CRM instead of duplicating them, skips anyone opted out or suppressed, and updates the open lead's score. Everything is written to the audit log and the timeline.
7. **Re-run.** A result older than 90 days (configurable) is *stale*. **Research → Re-run stale** queues those institutions again, and the newest result replaces the old one.

**Contact data rules (enforced in code)**

- An email is kept only if it appears on the cited page or a licensed provider reports it as verified.
- Pattern-guessed emails are discarded, and the reason is shown.
- The AI step can't add emails or phone numbers.
- A contact is marked *verified* only when a provider verified it.
- Every person and finding keeps its source link.

**Reliability and cost**

| Concern | How it's handled |
|---|---|
| Status | Job: queued → running → completed / partial / failed / cancelled. Run: queued → running → succeeded / failed / cancelled |
| Retries | Temporary errors (timeouts, rate limits, Claude 429/5xx/529) retry with doubling back-off, up to 3 attempts. Permanent errors (access not permitted, 4xx) fail at once. Failed runs can be retried from the Jobs view. |
| Rate limits | Per-provider limit, default 30 calls a minute |
| Cost | Provider cost plus Claude tokens × your configured price, stored per run and per job. A daily budget (default $5) defers further work to the next day. |
| Logging | Each run keeps a step-by-step log (provider calls, AI tokens, retries, errors), shown on the review page |
| Failure isolation | A crash on one institution never stops the worker. Nothing from a failed attempt is saved. |
| Worker | Runs inside the API by default (`RESEARCH_WORKER=embedded`). For a separate process, set `external` and run `python -m app.research.worker` (or `docker compose --profile worker up -d`). Several workers can run safely at once. |

**Settings** are listed in `.env.example` under Stage 2. Managers can change weights, tiers and freshness on **Research → Scoring**. Saving re-scores the latest results immediately.

**Adding a real provider.** Implement `ResearchProvider.research(...)` in `app/research/providers/`, call `register_provider("name", Factory)`, and set `RESEARCH_PROVIDER=name`. Only use official APIs, licensed data, or pages whose terms permit automated access. Never bypass logins, paywalls or robots rules.

## How to verify Stage 2 by hand

1. Sign in as `rep@plugdrop.example`. On **Institutions**, tick five demo institutions and choose **Research selected**. The **Jobs & queue** tab shows progress until the job is Completed.
2. On **Research → Results**, scores range across tiers. Open **Review** on one.
3. Check that every non-hypothesis item has a source link, quote and dates, that the hypothesis **Approve** button is disabled, and that guessed emails show "discarded".
4. Click **Approve all verified facts**, approve one person, then **Push to CRM**. The institution's Research tab now lists those findings with their evidence level, and the person appears in Contacts with source "Research agent".
5. Sign in as `manager@plugdrop.example`. On **Research → Scoring**, change a weight and save. The scores update.
6. Create an institution with website `https://flaky-test.example` and research it: it fails once, then succeeds on retry. With `https://blocked-test.example` it fails permanently, and the job shows *Partial* with a **Retry** button.

## Deployment

1. Provision PostgreSQL 16 and set `DATABASE_URL`.
2. Set `ENVIRONMENT=production`, a strong `JWT_SECRET`, `COOKIE_SECURE=true` and `SEED_PASSWORD`.
3. Build both images (`api/Dockerfile`, `web/Dockerfile`, with build arg `API_INTERNAL_URL` = the API's internal address).
4. Run the API container. It runs `alembic upgrade head` on start. Then run `python -m app.seed --users-only` once and change the seeded passwords (or create real users in Settings and deactivate the seeded ones).
5. Put both behind HTTPS and expose only the web service publicly. The API needs to be reachable only from the web container.
6. Back up Postgres. All CRM data lives there.

## Data protection

- Store only professional contact details. Record where each record came from (`source`), and add a LinkedIn URL only where it is lawfully available. The app stores the URL and never scrapes.
- Honour opt-outs. The API enforces them, so they apply even to scripts and integrations.
- Demo data is fictional. Don't add real prospect data to the demo seed.

## Not built yet

- outreach automation: sending email or WhatsApp, placing Voice AI calls (Stage 3)
- a licensed real-world research provider (the provider interface is ready; only the fictional mock is included)
- calendar booking
- email reminders for due tasks (reminders show in the app only)
- password reset and SSO
- per-record access restrictions (all signed-in users can see all records)

## Repository layout

```
api/                FastAPI app, Alembic migrations, tests, seed
  app/models.py     schema
  app/routers/      one module per resource
  app/services.py   duplicate detection, suppression, lookups
  app/research/     Stage 2: providers, reasoning (Claude/rules), scoring, job service, worker
web/                Next.js app
  src/app/(app)/    pages (dashboard, institutions, contacts, leads, opportunities, tasks, settings)
  src/components/   UI kit (shadcn-style), forms, timeline, app shell
docker-compose.yml  local stack
```

Branding follows the PlugDrop design system (purple `#8B5CF6`, ink `#080808`, Bricolage Grotesque / Manrope / JetBrains Mono), taken from the PlugDrop-AI website code. For accessible contrast, primary buttons use the deep purple `#6D28D9`.
