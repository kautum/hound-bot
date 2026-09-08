# PROJECT-WIKI.md — Slack Workplace Assistant

Read this before touching the code, even in a fresh low-effort session with no memory of how
this was built. It exists so you don't have to re-scan the codebase or re-derive decisions
that were already made and paid for once. `ARCHITECTURE.md` is the design doc (read it too —
this file is the *operational* companion: what's actually done, what's a known trap, and how
to use Devin correctly). The full plan with every decision's reasoning is at
`~/.claude/plans/alright-now-lets-glistening-yeti.md`.

**Last updated:** 2026-09-08, after an overnight autonomous fix-and-test run.

---

## -1. Tonight's run — read this if it's the morning after 2026-09-08

Two adversarial audits found 4 blockers and 12 real defects (2 critical security holes)
hiding behind the "112 tests passing" claim. Full plan and evidence:
`~/.claude/plans/alright-now-lets-glistening-yeti.md`, section "TONIGHT'S RUN". Short version:

**Fixed and tested tonight (138 tests passing, up from 112, against real Postgres):**
- Dev environment made runnable on this machine's native Postgres (no Docker, role
  `kpkautum` not `postgres`) — see `.env` and `Makefile`.
- A real migration/model drift test (`tests/test_migrations_match_models.py`), which
  immediately found and let us fix a real bug: `alembic/env.py` was ignoring any
  caller-supplied database URL.
- **S1** (critical): `/google/link` took a Slack identity as a plain query param —
  anyone could bind their own Google account to someone else's Slack identity. Fixed:
  identity never travels in a URL now; only an opaque, server-issued state token does.
- **S2** (critical): `UserRepository` had no tenant scoping at all, and `users`' primary
  key was `slack_user_id` alone — which is only unique *within* a workspace, not
  globally. Fixed with a schema migration to a composite `(team_id, slack_user_id)` key.
- **S3/S4**: completed tasks kept sending reminders forever; `/task done` had no
  authorization check at all. Both fixed.
- **S5**: `/meet book` ran two blocking Google API calls inline in the slash-command
  handler, over Slack's 3-second budget. Now ack-and-enqueue, like `/meet propose`.
- **S6/S7/S8/S9/S10**: key rotation support, worker crash supervision + real `/health`
  liveness, job/reminder claim-lock races under concurrent workers, Supabase pooler
  compatibility, and a URL-encoding crash in Alembic — all fixed with tests.
- Five tests that passed for the wrong reason (asserted too little to catch a real
  regression) were rewritten — four by Devin, one directly, after a time-budget cutoff.
- `LIVE-FIRE.md` — our Slack HMAC implementation cross-validated in both directions
  against `slack_sdk`'s own independent `SignatureVerifier`.

**Not done tonight, deliberately** (see the plan's "Nothing is asked of you before you
sleep" section): the real ngrok round trip, a real Groq call, a real Slack post,
GitHub repo + CI (Unit 9), and S11/S12 (analytics wiring, uninstall handling — Unit 10).
None of these need a login you haven't already provided except the three in the plan's
morning checklist (Slack install, `/link-calendar` consent, Render/Supabase/cron signups).

**Devin spend tonight:** one load-test task (committed), and 4/5 of a test-repair task
(the 5th finished by hand after a hard time cutoff — see the commit for what happened).
GPT-6 Astra was tried once and rejected instantly ("Upgrade to Pro") — not available on
this account; the default model was used instead.

---

## 0. Where we actually are right now — read this first if resuming

**Code is done (see §1). Since then, this session moved into live account provisioning** —
getting real Slack/Google/Groq/ngrok credentials so the built code can actually run. This
section goes stale fast; trust `.env`'s actual contents over this table if they conflict.

**All in `.env` already** (git-ignored, never committed): `PUBLIC_BASE_URL` (a real ngrok
domain), `SLACK_CLIENT_ID`/`SECRET`/`SIGNING_SECRET` (real), `GOOGLE_CLIENT_ID`/`SECRET`
(real), `GROQ_API_KEY` (real), `ENCRYPTION_KEY` and `CRON_SHARED_SECRET` (generated locally,
need no external account).

**Slack app — fully confirmed working.** Bot scopes, redirect URL, slash commands (`/task`,
`/meet`, `/link-calendar`), and event subscriptions (`app_mention`) were all set in one shot
via an **App Manifest** pasted into "Create an App → From an app manifest" — far more reliable
than clicking through OAuth & Permissions / Slash Commands / Event Subscriptions as separate
pages, which is where manual navigation got stuck originally. **If a new Slack app is ever
needed, use a manifest again, not manual clicking** — reconstruct the YAML from
`ARCHITECTURE.md`'s scope list and this repo's actual route paths (`/slack/commands`,
`/slack/events`, `/slack/oauth/callback`) if the original isn't in scrollback anymore.

**Google — one thing left unconfirmed.** Google Cloud Console has restructured what used to be
a single "OAuth consent screen" page into a "Google Auth Platform" section with separate
**Branding / Audience / Data Access / Clients** pages. A working Client ID + Secret were
obtained, but **whether the Calendar scopes (`calendar.freebusy`, `calendar.events`) are
actually present under "Data Access", and whether "Audience" has User Type = External with
test users added, was never independently confirmed** — getting a Client ID doesn't prove
those were set, given the new page structure. **Check this before assuming Google OAuth will
work end to end**, and before spending time debugging what might just be a missing scope.

**Local machine state, checked directly via Bash this session — not guessed:**
- Docker: **not installed/running** — `make db-up` as written won't work here
- A Postgres server **is already running on port 5432** (from an unrelated earlier project on
  this machine) — `.env.example`'s default `DATABASE_URL` will likely work against it
  directly; just create a `slack_workplace_assistant` database in it
- ngrok: authenticated, but **not currently running** — needs `ngrok http 8000` started before
  anything Slack/Google sends will actually reach this machine
- Nothing is listening on port 8000 — **the app itself has never been started.** Every "Done,
  tested" claim in §1 is true against mocked externals and a throwaway test Postgres; nothing
  has round-tripped through the real internet yet.

**Immediate next steps, in order, once resumed:**
1. Confirm Google's Data Access + Audience pages (the one unresolved item above)
2. Create the `slack_workplace_assistant` database in the existing port-5432 Postgres
3. `alembic upgrade head`
4. Start the app (`uvicorn app.main:app --reload`) and `ngrok http 8000` (background)
5. Visit `https://<the ngrok url>/slack/install` — the actual first live install, and the
   first moment any of this has touched the real internet

---

## 1. Build status — read this first, it's not all "done"

**Code: all 7 phases built. Tests: 112 passing, all against a real Postgres, none against real
Slack/Google/Groq.** Three passes of adversarial review during the build (two on the plan
before any code, one on the running code afterward) found and fixed real bugs each time —
treat any claim of "done" in this codebase with the same suspicion until you've verified it
yourself. See §6 for the specific bugs found, because the *pattern* of what got missed is more
useful than the list.

| Area | Status |
|---|---|
| Schema, migrations (0001–0005) | Done, verified up/down against real Postgres |
| Security primitives (HMAC, encryption, tenant isolation) | Done, tested, tenant-isolation test proven to have teeth (deliberately broken once to confirm it fails) |
| Slack OAuth install, event ingestion, dedupe, job queue | Done, tested against mocked Slack |
| Task CRUD, `/task` command, reminders, escalation | Done, tested |
| Google Calendar linking, `CalendarProvider`, availability intersection | Done, tested against mocked Google |
| Meeting scheduling: `/meet` propose + book, agent tool | Done, tested — **this took three review passes to actually work end-to-end**, see §6 |
| LLM agent loop (Groq), wired into real mentions | Done, tested against mocked Groq |
| Analytics digest | Done, tested |
| Dockerfile, `render.yaml`, CI | Done — CI was written by Devin, see §5 |
| **Block Kit UI** (buttons, App Home tab) | **Not built.** Every Slack reply is plain ephemeral text. Was always Devin's track in the plan, never started |
| **Observability, timeouts/backoff/pooling, load test** | **Not built** (plan's Phase 7.2/7.3/7.7) |
| **Live verification against real Slack/Google/Groq** | **Never done.** Every external API call in every test is mocked or faked. The code's *wiring* is proven (imports resolve, routes dispatch, Postgres round-trips work); the *external contracts* are based on documentation, not a live call |
| Devin's actual credit spend | **Unknown** — no CLI command exposes it; would need the web billing page |

**The honest one-sentence summary:** the backend is real, interconnected, and tested — not a
pile of individually-passing units — but nothing has run against the actual internet, and
there is no UI beyond text.

---

## 2. Module map

```
app/
  main.py              FastAPI app, lifespan (starts the in-process worker), /health
  worker.py            Claims inbound_jobs + reminders, does the slow work, posts to Slack
  scheduler.py         process_due_reminders — drained by /internal/tick

  api/
    routes_install.py    GET /slack/install, GET /slack/oauth/callback   (Slack OAuth)
    routes_google.py     GET /google/link, GET /google/oauth/callback    (Google OAuth)
    routes_events.py     POST /slack/events    (signature verify, dedupe, enqueue)
    routes_commands.py   POST /slack/commands  (/task, /meet, /link-calendar)
    routes_internal.py   POST /internal/tick   (cron-job.org, shared-secret protected)

  core/
    config.py    Settings — all env vars, all Optional, nothing required at import time
    db.py        Async engine/session factory
    security.py  verify_slack_signature, TokenCipher (Fernet + key_version)
    slack_client.py  Decrypts a workspace's bot token, builds an AsyncWebClient

  models/        SQLAlchemy models — one file per table, see ARCHITECTURE.md's ER diagram
  repositories/  Data access. TenantScopedRepository (base.py) enforces team_id on everything
                 tenant-scoped. Workspace/InboundJob/Reminder are NOT tenant-scoped (see why
                 in each file's docstring — they're the tenant root, or claimed cross-tenant
                 by the worker).
  services/      Business logic: task_service, meeting_service, analytics_service,
                 slack_oauth, task_command_parser, meet_command_parser
  calendar/      provider.py (the Protocol), google_calendar.py (the implementation),
                 intersection.py (the availability-intersection function)
  agent/         tools.py (the whitelist + AgentContext), loop.py (the Groq-calling loop)
```

## 3. Entry points and data flow

- **A Slack event arrives** → `routes_events.py` verifies the signature on the *raw* body →
  dedupes on `event_id` → enqueues an `InboundJob` → returns `200`. The worker
  (`worker.py:process_one_batch`, polled every 2s locally / triggered by every request in
  prod — see the free-tier note in §4) claims it and does the real work.
- **A slash command arrives** → `routes_commands.py` dispatches on the `command` field.
  `/task` and `/link-calendar` are fast DB-only operations, answered inline. `/meet`'s
  propose path is **not** — see §6, item 1, for why it's the one command that enqueues
  instead of answering inline.
- **The 5-minute cron tick** hits `/internal/tick`, which calls `scheduler.py`'s
  `process_due_reminders` — drains due reminders with `FOR UPDATE SKIP LOCKED`, same pattern
  the job queue uses.
- **A mention** (`app_mention` event) → enqueued → `worker.py:handle_app_mention` → builds an
  `AgentContext` → `agent/loop.py:run_agent_turn` → the model picks a tool from
  `agent/tools.py`'s whitelist (currently `create_task`, `list_tasks`, `propose_meeting` — no
  `book_meeting` tool; booking is deterministic-only via `/meet book`, a documented scope
  decision, not an oversight) → the real service function runs → the reply posts back to Slack.

## 4. Config

Everything lives in `app/core/config.py`'s `Settings`, sourced from `.env` (copy
`.env.example`). Nothing is required at import time — tests that don't need a given
integration just leave it `None`, and the code that needs it fails loud at the point of use
(`RuntimeError`), never silently.

**The free-tier constraint that shapes the architecture** (see `ARCHITECTURE.md` for the full
reasoning): one Render service, ever; Supabase via the **pooler on port 6543**, not direct
5432; Groq's 8,000 TPM is the binding limit, not its per-day quota.

---

## 5. How to use Devin — read this before delegating anything

### The invocation that actually works, discovered this session

Devin refuses to run non-interactively in a directory it hasn't been told to trust, and
refuses to write files without explicit permission in non-interactive mode. Both need
flags — there is no prompt to click through in `-p` mode:

```bash
devin --respect-workspace-trust false --permission-mode dangerous -p -- "
Context: Read ARCHITECTURE.md and CONTRIBUTING.md first, in this repo.
Goal:    <one sentence>
Touch:   <explicit file/directory list — be exact>
Accept:  <machine-checkable, e.g. 'ruff check app tests passes'>
Do NOT:  touch app/core/, app/models/, alembic/, or anything outside Touch.
         Do not add new dependencies without saying why.
"
```

`--permission-mode dangerous` auto-approves *every* tool call Devin makes, not just file
writes — only use it for a task scoped tightly enough that you're comfortable with that.
CI (`.github/workflows/ci.yml`) was built this way and it worked, but it also demonstrates
why review still matters: **Devin wrote `DATABASE_URL: postgresql://...` instead of
`postgresql+asyncpg://...`**, which would have broken every CI run against our async engine.
Caught on review, fixed directly rather than re-billing a round trip for a one-line fix. Read
every Devin diff before trusting it, even a successful-looking one.

### What to actually delegate — the plan's Part 5 rule, restated for what's left

Delegate when: success is machine-verifiable, iteration is high/unpredictable, the spec is
short relative to the output, and a bug is low blast radius. Keep it yourself when: the spec
*is* the hard part, a bug would be catastrophic and invisible in review, or it depends on
decisions made in conversation.

**Genuinely good Devin candidates remaining** (all Infrastructure-track, `tests/`, `.github/`,
`app/ui/`, `app/observability/` — never `app/core/`, `app/models/`, `app/repositories/`,
`app/services/`, `app/agent/`, `alembic/`):

| Unit | Ready-to-paste Goal/Touch | Why it fits |
|---|---|---|
| Block Kit UI | Goal: "Replace plain-text ephemeral replies in `app/api/routes_commands.py` with Block Kit messages for `/task list` and `/meet` propose/book, without changing any function signature or return type — just the `text` field's content shape via `blocks`." Touch: `app/api/routes_commands.py`, new `app/ui/blocks.py` | Verbose, needs post→look→fix iteration, zero judgment calls |
| Observability | Goal: "Add structured JSON logging (one line per request/job with team_id, duration, outcome) and confirm `/health` still returns 200." Touch: `app/main.py`, `app/worker.py`, new `app/observability/` | Mechanical, low blast radius. **Do not add Sentry** — its free tier's card requirement is disputed across sources, unverified |
| Timeouts/backoff/pooling | Goal: "Add exponential backoff with a max of 3 retries to every outbound `httpx` call in `app/calendar/google_calendar.py` and `app/core/slack_client.py`'s underlying client; verify existing tests still pass." | Well-known pattern, machine-verifiable |
| Load test | Goal: "Write a script that hits `/slack/events` with a valid signature N times concurrently and reports p50/p95/p99 latency, proving the <3s ack holds under load." Touch: new `scripts/load_test.py` | Pure measurement, no judgment |

**Do not delegate:** anything touching `app/agent/tools.py`'s whitelist (security boundary),
`app/services/meeting_service.py` (this session found three real bugs in it — organiser
availability, unpersisted slot, missing authorization check — exactly the "catastrophic and
invisible in review" category), or the schema/migrations.

---

## 6. Known gotchas — read before you hit these again

1. **Any new command that makes a network call must ack-and-enqueue, never answer inline.**
   `/meet`'s propose path originally called Google's freebusy API synchronously inside the
   slash-command handler — passed every test (tests don't run Slack's 3-second clock) and
   would have silently timed out in production the first time someone proposed a meeting with
   more than one or two participants. Fixed by enqueueing an `InboundJob` and replying via
   Slack's `response_url`. **Before adding any new command that touches Google/Groq/any
   external API, ask: could this exceed 3 seconds? If yes, enqueue it.**

2. **SQLAlchemy won't auto-order inserts across tables that lack a declared `relationship()`.**
   This codebase uses plain `ForeignKey` columns with no ORM `relationship()` (deliberate —
   simpler, and tenant-scoping is enforced in the repository layer instead). Consequence:
   `session.add(Workspace(...)); session.add(User(...)); await session.commit()` can fail with
   a `ForeignKeyViolationError` if SQLAlchemy happens to flush `User` first. **Always
   `await session.flush()` after adding a parent row, before adding a row that FKs to it**, in
   the same session. Every test helper in `tests/` already does this (see `_make_workspace` in
   any test file) — copy that pattern, don't inline two `session.add()` calls back to back.

3. **Testing async FastAPI routes: use `httpx.AsyncClient` + `ASGITransport`, never the sync
   `TestClient`, if a test also touches the DB session directly.** `TestClient` runs the app
   in a separate thread with its own event loop; a route and the test sharing one asyncpg
   session across two different loops fails with `RuntimeError: ... attached to a different
   loop`. The `api_client` fixture in `tests/conftest.py` already does this correctly — use it,
   don't build a new one.

4. **The local throwaway-Postgres test loop can appear to "lose" tables between sessions.**
   `tests/conftest.py`'s `db_engine` fixture creates the schema with `Base.metadata.create_all`
   and drops it with `drop_all` *per test*, on whatever `DATABASE_URL` you point at — including
   a real persistent database if you point it at one instead of a scratch DB. If you manually
   run `alembic upgrade head` against the same database you also run `pytest` against, the last
   test's teardown drops everything, leaving `alembic_version` claiming a revision that no
   longer matches reality. **Use a genuinely separate, disposable database for manual
   migration checks** (`make db-up` gives you docker-compose's Postgres for exactly this;
   don't share it with a database you're also using for something you want to persist).

5. **Groq's real per-minute token budget (8,000 TPM) is the binding constraint, not its
   per-day request quota.** Keep tool schemas in `app/agent/tools.py` minimal. This was a
   documentation-vs-reality gap caught by an earlier audit pass, not by testing — Groq's own
   docs list a much larger per-*day* number for a *different* model (a safety classifier) that
   isn't the one we use.

6. **The Devin credentials issue from §5** is the concrete proof that "Devin's PR passed CI"
   is not the same as "Devin's PR is correct" — CI here would have been green (the workflow
   file itself is valid YAML; the bug only manifests when the workflow actually *runs* against
   a real async engine, which nothing in the PR verified).

---

## 7. Local dev, quick reference

```
cp .env.example .env
make db-up       # docker-compose Postgres
make migrate     # alembic upgrade head
make test        # pytest — self-provisions schema per test, doesn't need `make migrate` first
make lint        # ruff check app tests
make run         # uvicorn, hot reload
```

If Docker isn't available (it wasn't in the environment this was built in), a native
`postgres` install works identically — `initdb`, `pg_ctl start -o "-p 5433 -k /tmp"`, then
point `DATABASE_URL` at `postgresql+asyncpg://postgres@/dbname?host=/tmp&port=5433`. Don't
share that instance between manual `alembic` runs and `pytest` runs — see gotcha #4.
