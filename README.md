# PlugDrop.AI Sales CRM (internal)

The PlugDrop.AI sales team uses this CRM to sell Voice AI + CRM solutions to universities, colleges, university groups, EdTech companies, test-prep businesses and vocational/professional education providers.

**Stage 1** is the foundation (below); **Stage 2** adds AI prospect research and **Stage 3** an AI outreach agent. Stage 1: a CRM backed by a database, with institutions, contacts, a 12-stage lead pipeline, opportunities, activity timelines, tasks, CSV import/export, duplicate detection, opt-out/suppression and a full audit history.

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
pytest                 # 201 tests against a real Postgres (plugdrop_test; override with TEST_DATABASE_URL)
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

## Importing the team's research workbook

The research team's Excel file ("PlugDrop.AI – Indian Higher-Ed Admissions Intelligence Database") can be loaded on **Research → Import workbook** (managers and admins). Choose the file, click **Preview** to see what will happen, then **Import**. Always use the newest file: each batch contains all earlier ones, and re-importing is safe because unchanged institutions are skipped.

What it does:

| Workbook sheet | Becomes |
|---|---|
| Master_List | One institution per row (country India, state and address in notes, workbook account id `PD-nnn`). Universities with the same name in different states stay separate, e.g. "Twin University (Rajasthan)". |
| Account_Intelligence, Sales_Intelligence, Technology_Map, Sources | A research result per researched institution: triggers, current situation, gaps, technology seen and PlugDrop relevance as evidence with the source link; opportunity signal, best entry point and admissions model shown on the review page. A lead is created at stage *Researched* (not for accounts marked ON HOLD). |
| Contact_Database | People to review, with salutation ("Prof.", "Dr.") and notes (confidence, role verified, buying role, priority, phone type). |
| Data_Quality, conflicting names | Listed under *Missing information* on the review page. |

Rules: an email is kept only where the workbook says **VERIFIED**, and it is stored as *published on source*, not as a tested, deliverable address. UNVERIFIED or "not used" emails and phones marked UNVERIFIED or DO NOT USE are dropped (kept in the person's notes for reference). Rows with conflicting names or no named person and no contact details are not turned into people. Statements labelled INFERENCE/REPORTED/UNVERIFIED stay inferences. **Nothing reaches contacts or findings until someone approves it** on the research result and clicks **Push to CRM**; only then can outreach start.

The CRM score (out of 100) counts only evidence it can classify, so a workbook account may score lower than the researcher's STRONG/MODERATE rating. Both are shown. From the command line: `python -m app.research.workbook FILE.xlsx` (preview) or `--commit`.

## Stage 3: AI outreach agent

Drafts personalised B2B emails and follow-ups from **approved research only**, and records everything in the CRM. A person approves every email. Calls are made by the team, not the system.

**Workflow**

1. **Start outreach** (Outreach page or a lead) → choose a prospect whose research was approved and pushed, a contact with an email, a mailbox and optionally a use case. Contacts that can't be emailed are listed with the reason (no email, opted out, bounced, no approved facts, already in a sequence).
2. The writer uses only approved facts and the approved **content library**. It drafts the first email (one observation from research + one problem + one outcome + one low-friction question) and the follow-ups. Claude writes when `ANTHROPIC_API_KEY` is set; otherwise rule-based templates. Research notes are never pasted into an email: when no fact has approved wording, the opening line is left as an `[EDIT …]` for a person to write (approval is blocked until they do). By default only **one person per institution** can be in outreach at a time (policy setting).
3. Every draft passes **quality checks** before it can be approved: length, specific to the institution, built on an approved fact, inferences worded as observations, no internal research notes, no hype or banned phrases, nothing implying an existing relationship, no numbers/ROI not in an approved source, no integrations, product capabilities, other institutions' names or case studies outside the approved library, a clear question, no placeholders, an opt-out line and a one-line subject. The checks are a safety net, not a guarantee: a person still reads every email.
4. **Edit, Redraft, Approve, Schedule, Send now/Put in mailbox, Cancel.** Two people pressing Send at the same moment still send one email. Editing an approved email sends it back to draft. Follow-ups are scheduled after the previous email using the policy spacing (default 0, 4, 7 days; max 3 emails).
5. **Before each send** the system re-checks: opt-out/suppression, bounce, lead closed, any reply, recipient changed, mailbox approved, previous email sent, sending window, mailbox/global/per-institution daily limits, and that the person wasn't emailed in the last 20 hours. A send is recorded as "sending" before the provider is called, so a retry can never send twice.
6. **Replies and delivery** are synced from the mailbox. A reply is matched to its sequence and classified as Interested, Meeting request, Needs information, Not now, Not interested, Referral, Out of office or Unclear (anyone can correct the label). Follow-ups **stop** on any reply, opt-out ("unsubscribe", "remove me", …), bounce, suppression or **Not a fit**. Out-of-office notices and automatic "we have received your query" acknowledgements pause follow-ups for 7 days instead of stopping them. Quoted text from our own email (including its unsubscribe line) is ignored when reading a reply. An opt-out is written to the suppression list. Interested replies create a task and move the lead to *Engaged*. **Draft a reply** writes a response for review.
7. Every action and result is logged on the sequence, on the lead's timeline and in the audit log.

**Mailboxes and sending (safe by default)**

- Providers: Microsoft 365 (Graph) and Google Workspace (Gmail API), connected with OAuth. Passwords are never asked for or stored. Tokens are encrypted with `TOKEN_ENCRYPTION_KEY`. Scopes are kept small: Microsoft asks for mail read/write and adds `Mail.Send` only after the policy is switched to sending; Gmail uses `gmail.compose` (drafts and sending) and `gmail.readonly` (reply and bounce sync), because Google has no draft-only scope. Either way, the app itself only sends when all three switches below are on.
- A personal mailbox can only be used by its owner. Whoever connects a mailbox records who authorised it, and **an admin must approve it** before use.
- **Draft-only** is the default: approved emails are placed in the mailbox as drafts for a person to send. After sending it, press **I've sent it** on the sequence; follow-ups only start counting from then, so a follow-up never refers to an email that was never sent. Sending needs three things: policy set to *Send* (admin), *Allow sending* on the mailbox (admin), and `OUTREACH_EXTERNAL_SENDING_ALLOWED=true` on the server. That last switch is **off**, so real mailboxes can't send until configuration and testing are complete.
- The **test (mock) mailbox** keeps everything inside the system. Addresses containing "bounce" bounce, and **Simulate reply** on a sequence lets you test reply handling.

**Settings → Outreach** (admin): mailboxes, messaging policy (mode, attempts, spacing, limits, sending window and time zone, word limits, signature, opt-out line, banned phrases, restricted product names), content library (managers add; admins approve; edits need re-approval) and templates. Demo content is marked DEMO and based on the public plugdrop.ai FAQ; replace it with your approved messaging.

## How to verify Stage 3 by hand

1. Sign in as `admin@plugdrop.example`. In **Settings → Outreach**, set the sending window to 0–24 and the mode to *Send*, then click **Allow sending** on the test mailbox.
2. Research a demo institution (Stage 2 steps), approve the facts and one person with an email, and **Push to CRM**.
3. **Outreach → Start outreach** → choose that prospect and person → **Draft emails**. Check the quality checks and "Why this email?".
4. **Edit** the first email, save, **Approve**, **Send now**. It shows *Delivered*; the lead timeline shows "Email sent"; follow-up 1 can be approved and scheduled.
5. **Simulate reply** "Can we talk Thursday at 3pm?" → classified *Meeting request*, sequence *Stopped: replied*, follow-ups *Cancelled*, a task is created.
6. Start a second prospect, send, then simulate "Please unsubscribe me" → *Stopped: opted out*, listed under **Opt-outs & suppressed**, and a new sequence for that person is refused.

The same flow is covered by the automated acceptance test `api/tests/test_outreach_flow.py`.

## Stage 4: Coordinated engagement (WhatsApp, meetings, follow-ups, LinkedIn)

Takes a prospect from first interest to a booked meeting across email and WhatsApp, with one timeline per contact. A person approves every outgoing message and every meeting.

**One timeline, one record (channel coordination)**

- Every email, WhatsApp message, meeting, task, LinkedIn action and research step is written to the lead's timeline. The lead page has an **All interactions** tab with channel filters; meetings and the WhatsApp inbox show the same timeline for the person.
- **Before any outreach** (email follow-up, WhatsApp, LinkedIn draft) the system checks the shared record: opt-out/suppression, the current conversation state (waiting for our reply, meeting being arranged or booked), open tasks, and recent messages on *any* channel. The result is *block*, *wait* or *warning*, shown to the user with the reason. Examples: an email follow-up waits if the person had a WhatsApp message in the last 20 hours; nothing automatic goes out while a meeting is being arranged.
- An opt-out on either channel ("STOP" on WhatsApp, "unsubscribe" by email) stops both channels.

**WhatsApp (Module A)**

- Uses the **official WhatsApp Business Platform (Meta Cloud API)** behind a provider layer. A **test (mock) provider** keeps everything inside the system. Personal WhatsApp accounts are never automated.
- A person can be messaged only after **opt-in is recorded** (how and when they agreed). Numbers found by research are never messaged.
- Outside the 24-hour customer-service window only **approved templates** can be sent. Templates are managed in Settings (managers add, admins approve; marketing templates must include an opt-out line).
- Incoming messages arrive through a webhook whose signature is checked; duplicate deliveries are ignored. Each message is linked to the contact, classified (interested, meeting request, needs information, not now, not interested, opt-out, unclear) and given a **suggested reply** that a person edits and approves.
- **Hand off to a person** stops suggestions for a conversation and creates a task for its owner.
- Delivery status (sent, delivered, read, failed) and errors are shown per message. Failed sends retry with back-off; per-minute and daily limits apply; a message is marked "sending" before the provider is called so it can never go twice.

**Meeting assistant (Module B)**

- When a reply asks for a meeting or shows interest, the assistant reads the request (e.g. "Thursday at 3pm"), suggests an agenda from the approved research, proposes free times from the owner's **connected calendar** (working hours, India time by default) and drafts the reply for approval.
- The calendar event is created **only after someone presses Approve and book**. Attendees, notes, outcome and next steps are saved to the CRM; the lead moves to *Meeting booked*.
- Calendars: Google and Microsoft 365 over OAuth (tokens encrypted), each approved by an admin. Test calendars are used for demos.

**Follow-up tasks (Module C)**

Created automatically, never duplicated, each with an **owner, due date, reason and source activity**: reply needed, interested but no meeting booked, meeting needs confirmation, post-meeting follow-up, proposal follow-up, and reconnect later ("try us in March" → a task in March). They close themselves when the condition is met (e.g. the meeting gets booked). A background check runs every 30 seconds.

**LinkedIn**

Research notes, connection-note drafts (300-character limit) and post ideas only. There is no scraping, no automatic connection requests and no LinkedIn messaging: a person copies the text into LinkedIn and records that it was done.

**Admin controls (Settings → WhatsApp, calendars & permissions)**

WhatsApp on/off, limits, quiet time between touches, meeting hours, follow-up timings, templates, calendar approvals, and the **minimum role for each action** (e.g. who can approve templates or book meetings). All actions write structured logs with phone numbers masked and message bodies left out.

### Setting up real WhatsApp (when ready)

1. In Meta Business Manager, verify the business and create a WhatsApp Business account and phone number.
2. Create a System User token with `whatsapp_business_messaging` and `whatsapp_business_management`.
3. In `.env`: `WHATSAPP_PROVIDER=meta`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` (any long random text).
4. In Meta's webhook settings use `https://<your-domain>/api/whatsapp/webhook`, the same verify token, and subscribe to `messages`.
5. Create the templates in Meta, add them in Settings with the same names, and approve them.
6. Test with your own opted-in numbers. Only then set `WHATSAPP_EXTERNAL_SENDING_ALLOWED=true` and switch WhatsApp on in Settings. Until both are on, nothing is sent to real numbers.

### Setting up calendars

Reuse the Google and Microsoft OAuth apps from Stage 3 and add the redirect URIs listed in `.env.example`. Calendar scopes: Google `calendar.events` + `calendar.freebusy`; Microsoft `Calendars.ReadWrite`. Each user connects their calendar in **Settings → WhatsApp, calendars & permissions**; an admin approves it. Set `CALENDAR_MOCK_ENABLED=false` in production.

## How to verify Stage 4 by hand

1. Sign in as admin. **Settings → WhatsApp, calendars & permissions** → switch WhatsApp on (test provider). Test calendars are already connected for the demo users.
2. Send a first email to a prospect (Stage 3 steps), then **Simulate reply** "Could we speak on Thursday at 3pm?".
3. Check: sequence *Stopped: replied*, follow-ups *Cancelled*; a **meeting** appears under Meetings with Thursday 3pm marked "the time they asked for"; a reply email with the times is drafted; tasks "Reply needed" and "Interested – no meeting yet" show owner, due date, reason and source.
4. Approve and send the reply. On the meeting, pick the time → **Approve and book**. The lead moves to *Meeting booked* and a confirmation task appears.
5. On the lead, **People and channels → WhatsApp** → record opt-in. In the WhatsApp inbox type a message under **Test: simulate a message from them**, then **Use suggested reply → Approve & send**.
6. The lead's **All interactions** tab shows the email, reply, meeting, tasks and WhatsApp messages in one timeline.
7. Send "STOP" from the simulated WhatsApp number → the contact is opted out on both channels.

The acceptance test is automated in `api/tests/test_engagement.py` (24 tests: acceptance flow, coordination, WhatsApp rules, webhook signatures and duplicates, retries, meetings, tasks, LinkedIn, permissions, log masking). All external services are replaced by test providers in the tests.

## Checking the Docker setup

Docker can't be run in the environment this was built in, so the stack was tested without it, and a check script is provided. On a computer with Docker Desktop:

```bash
cp .env.example .env     # set POSTGRES_PASSWORD, JWT_SECRET and SEED_PASSWORD (any long random text)
./scripts/docker-smoke-test.sh                       # builds, starts, seeds, signs in, calls every module
./scripts/docker-smoke-test.sh path/to/workbook.xlsx # also previews a workbook import (nothing saved)
```

It prints PASS/FAIL per check and leaves the app running at http://localhost:3000 (`docker compose down` stops it). GitHub Actions runs the same script on every pull request (`docker` job), plus a check that the API starts with only the production dependencies.

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

- Voice AI calls (calls are made by the team)
- live WhatsApp sending: code is ready, but needs the Meta business account, approved templates and testing before `WHATSAPP_EXTERNAL_SENDING_ALLOWED` is turned on
- live Google/Microsoft calendars: code is ready, needs OAuth apps and testing
- live Gmail/Microsoft 365 connection: code is ready, but needs OAuth apps registered and testing before `OUTREACH_EXTERNAL_SENDING_ALLOWED` is turned on
- a licensed real-world research provider (the provider interface is ready; only the fictional mock is included)
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
