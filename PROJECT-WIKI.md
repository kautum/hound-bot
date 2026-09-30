# PROJECT-WIKI.md — Hound

Hound is a multi-tenant Slack bot: it chases overdue tasks with DMs and finds the earliest mutual
free slot across teammates' Google Calendars. Public repo: https://github.com/kautum/hound-bot.
(The package is still named `slack-workplace-assistant` in `pyproject.toml`; that mismatch is
cosmetic and deliberate.)

This file is the operational map: what exists, how requests flow, what bites. It is meant to be
read instead of re-scanning the code. Design rationale is in `ARCHITECTURE.md`; chronology and
post-mortems are in `docs/BUILD-LOG.md` (history, not instructions). Facts here are machine-checked
by `scripts/check_docs.py` (`make docs-check`, also a CI step), within limits: it verifies that each
single backticked repo path exists and that the test-count and migration-head markers equal the
truth (markers inside code fences are ignored, though fence parsing is approximate for unusual
markdown, and a pytest run that errors fails the check instead of guessing). It does not check
prose or paths written inside a list, and a path with a line range such as `:10-20` is reported as
missing, so write it without the range.

**Status, 2026-09-30.** Feature-complete and merged to `main` (PR #1, 2026-09-29), CI green.
Tests: 222 collected <!-- check:test-count=222 -->. Migration head: 0009 <!-- check:migration-head=0009 -->.

## YOUR TASKS

Only the owner can do these. Claude Code's auto-mode blocked agents from items 1 and 3 (they change
the live database or act on the live Slack workspace and Google account), so they are left here on
purpose.

1. **Migrate the live database and restart the server.** The live DB (`slack_workplace_assistant`)
   is at migration 0008 and the code needs 0009 (`tasks.recurrence_interval_days`). The running
   uvicorn started 2026-09-17, before recurring tasks existed. A backup taken 2026-09-30 is at
   `~/hound-backups/slack_workplace_assistant-pre0009.sql`. Then:
   `CONFIRM_LIVE_ALEMBIC=slack_workplace_assistant .venv/bin/alembic upgrade head`, stop the old
   uvicorn, start `.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`.
2. **Google Calendar link.** Publish the OAuth app to "In production" (Cloud Console, Audience,
   Publish app) *before* re-linking with `/link-calendar`, or the refresh token dies again after
   7 days. See `LIVE-FIRE.md` for whether the current token is alive.
3. **Live-fire steps 3 to 5** (real Google freeBusy, `/meet` propose, `/meet` book), after items 1
   and 2, so the server runs current code. From the repo root export `TEST_TEAM_ID`
   (`select team_id from workspaces`), `TEST_USER_ID` (`select slack_user_id from users`) and
   `TEST_UNLINKED_USER_ID` (any well-formed nonexistent ID, for example `UAAAAAAAAA`), then run
   `.venv/bin/python scripts/live_fire.py --step 3`, then `--step 4`, then `--step 5 --meeting-id <id>`
   with the id step 4 prints. Step 3 also tells you whether the Google token is alive. Afterwards run
   `/meet cancel <id>` in Slack to delete the booked event. Paste the raw output, with IDs
   redacted, into `LIVE-FIRE.md`. If a step fails, that is the finding; hand it to `hound-builder`.
4. **Record the demo** following `DEMO-SHOTLIST.md`, save it as `demo.mp4` at the repo root, and
   embed it in `README.md`.
5. **Merge the open docs/subagents PR**, https://github.com/kautum/hound-bot/pull/2 (agents may not
   merge to `main`).

## 1. Run it locally

```
cp .env.example .env   # fill in values; .env is git-ignored, never commit or paste it
make db-up             # native Postgres (brew services); creates the live and swa_test databases
make migrate           # alembic upgrade head, guarded: see section 6
make test              # pytest against swa_test; builds its own schema per test
make lint              # ruff check app tests
make docs-check        # scripts/check_docs.py
make run               # uvicorn --reload on :8000
```

Databases on this machine: `slack_workplace_assistant` (live, seeded with the real workspace and
linked token), `swa_test` (tests, reviewer), `swa_devin` (builder scratch), `swa_migration_check`
(migration drift test). Slack reaches the laptop through an ngrok static domain
(`ngrok http --domain=<your-domain> 8000`); `PUBLIC_BASE_URL` in `.env` must match it.
`GET /health` returns 503 `degraded` if `ENCRYPTION_KEY` is set and the worker has not polled in
30 seconds (this includes the first moments after startup, before the first poll), otherwise 200.

## 2. Module map

```
app/main.py                 FastAPI app, lifespan (starts the worker if ENCRYPTION_KEY is set), /health
app/worker.py               Claims inbound_jobs every 2s, runs HANDLERS, posts results to Slack
app/scheduler.py            process_due_reminders, drained only by POST /internal/tick
app/api/routes_install.py       /slack/install, /slack/oauth/callback
app/api/routes_google.py        /google/link, /google/oauth/callback
app/api/routes_events.py        /slack/events
app/api/routes_commands.py      /slack/commands
app/api/routes_interactions.py  /slack/interactions (Block Kit buttons)
app/api/routes_internal.py      /internal/tick
app/core/config.py          Settings (env vars). app/core/db.py async engine. app/core/security.py
                            Slack signature verify + TokenCipher (Fernet with key_version).
                            app/core/slack_client.py builds a workspace's AsyncWebClient
app/models/                 workspace, user, task, reminder, meeting (meetings + meeting_participants),
                            inbound_job, operational (processed_events + oauth_states), base
app/repositories/           data access; app/repositories/base.py is TenantScopedRepository
app/services/               task_service, meeting_service, analytics_service, slack_oauth,
                            task_command_parser, meet_command_parser
app/calendar/               provider.py (CalendarProvider Protocol), google_calendar.py,
                            intersection.py (earliest mutual slot)
app/agent/                  tools.py (whitelist + AgentContext), loop.py (Groq tool-calling loop)
app/ui/blocks.py            Block Kit builders (task list, App Home, meeting buttons)
alembic/versions/           migrations 0001 to 0009
scripts/live_fire.py        drives real Slack/Google calls once each; scripts/load_test.py checks the 3s ack budget
scripts/check_docs.py       docs drift check
```

There is no Bolt: the app uses `slack-sdk`'s `AsyncWebClient` and does its own signature check.

## 3. How requests flow

- **Events** (`app/api/routes_events.py`): verify the Slack signature over the raw body (5-minute
  replay window, constant-time compare), answer `url_verification`, dedupe on `event_id`
  (`processed_events`), enqueue an `InboundJob` only for `app_mention`, `app_uninstalled` and
  `app_home_opened`, return 200.
- **Slash commands** (`app/api/routes_commands.py`, signature verified):

  | Command | Handled |
  |---|---|
  | `/task add \| list \| done \| reassign` | inline, DB only. `done` and `reassign` allow the assignee or creator only. `list` returns Block Kit |
  | `/link-calendar` | inline: issues an `oauth_states` row, returns `/google/link?state=...` |
  | `/meet <@users> <min> \| <start> \| <end>` | enqueues job `meet_propose` (1 to 7 participants) |
  | `/meet book <id>` | enqueues `meet_book` |
  | `/meet cancel <id>` | enqueues `meet_cancel` |

  All three `/meet` forms reply inline "Calendar scheduling isn't configured" and enqueue nothing
  unless the Google and encryption settings are all set. `/meet book` and `/meet cancel` do no
  organiser check inline; `meeting_service` enforces it in the worker.

- **Buttons** (`app/api/routes_interactions.py`, signature verified, first action only):
  `task_done` runs `mark_task_done` inline through the same authorization as `/task done`;
  `meet_book` enqueues (organiser-only is enforced later, in the worker); `meet_cancel` checks
  the organiser inline, then enqueues. Anything else replies "Unknown action."
- **Worker** (`app/worker.py`): `HANDLERS` maps `app_mention`, `meet_propose`, `meet_book`,
  `meet_cancel`, `app_uninstalled`, `app_home_opened` to functions. An unknown type or a raised
  exception marks the job failed and logs it; nothing is silently marked done. The worker never
  sends or claims reminders (the `create_task` agent tool can still create reminder rows).
- **Reminders**: `POST /internal/tick` (header `X-Cron-Secret`, `hmac.compare_digest`) runs
  `process_due_reminders` (`FOR UPDATE SKIP LOCKED`, skips reminders whose task is not open),
  then deletes `processed_events` older than 7 days. Nothing else drives it: in dev, POST to it
  yourself; in production cron-job.org would, every 5 minutes. Reminder DMs carry a Mark done button.
- **@mention** goes `handle_app_mention`, then `app/agent/loop.py` (Groq `openai/gpt-oss-120b`),
  then one of four tools: `create_task`, `list_tasks`, `get_digest`, `propose_meeting`. No tool
  takes `team_id` or `slack_user_id`; both come from `AgentContext`. There is no book or cancel tool.
- **Installs**: Slack `/slack/install` then `/slack/oauth/callback` (state checked, workspace upserted,
  HTML page, best-effort welcome DM). Google `/link-calendar` then `/google/link?state=` then Google
  then `/google/oauth/callback` (stores the encrypted refresh token). Identity never travels in a URL.

## 4. Data model

Tenant scoping is by `team_id`. `TenantScopedRepository` covers tasks and users; the workspace,
job, reminder, processed-event and oauth-state repositories are intentionally not scoped (tenant
root, cross-tenant worker claims, or keyed by opaque IDs).

`workspaces` (PK team_id) · `users` (PK `(team_id, slack_user_id)`) · `tasks` (uuid, has
`recurrence_interval_days`) · `reminders` (has `claimed_at`, denormalised `team_id`) · `meetings` ·
`meeting_participants` · `inbound_jobs` (pending, claimed, done, failed) · `processed_events` ·
`oauth_states`.

| Migration | Does |
|---|---|
| 0001 | initial schema |
| 0002 | `inbound_jobs` |
| 0003 | `users.google_link_broken_at`, `google_email` |
| 0004 | `created_at` on tasks and meetings |
| 0005 | `meetings.proposed_start_utc` |
| 0006 | composite `users` primary key `(team_id, slack_user_id)`; `meeting_participants` foreign key rebuilt as composite |
| 0007 | `reminders.claimed_at` |
| 0008 | `processed_events` index and 7-day cleanup |
| 0009 | `tasks.recurrence_interval_days` |

## 5. Config

`app/core/config.py` `Settings`, from `.env` (template `.env.example`). Only `DATABASE_URL` is
required at import. Everything else is optional and fails loudly where it is used:
`SLACK_SIGNING_SECRET` (the three signed routes, `/slack/events`, `/slack/commands` and
`/slack/interactions`, return 500 without it), `SLACK_CLIENT_ID` and
`SLACK_CLIENT_SECRET` (install), `PUBLIC_BASE_URL` (install, Google, commands),
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (Google and `/meet`), `ENCRYPTION_KEY` (worker start,
token cipher), `GROQ_API_KEY` (@mention replies), `CRON_SHARED_SECRET` (`/internal/tick`).
`ENCRYPTION_KEY_VERSION`, `ENCRYPTION_KEY_OLD` and `ENCRYPTION_KEY_OLD_VERSION` exist for key
rotation; the last two are not in `.env.example` or `render.yaml`.

## 6. Safety rails for the live database

- `tests/conftest.py` refuses to run unless the database is `swa_test` or `swa_devin` (no override),
  because it drops every table per test.
- `alembic/env.py` refuses any database outside `swa_test`, `swa_devin`, `swa_migration_check`
  unless `CONFIRM_LIVE_ALEMBIC=<exact db name>` is set for that one call. This exists because an
  agent once ran `alembic downgrade base` against the live DB (see `docs/BUILD-LOG.md`, section 0.10).
- Always `pg_dump` before migrating the live DB. Agents must not migrate it; the owner does.
- Never write an agent spec that says "use whatever DATABASE_URL is in the environment": `.env`
  points at the live database.

## 7. Working with subagents

Delegation is by **typed subagents only**, never forked chats. Roles live in `.claude/agents/`:

| Agent | Job | Database | May edit |
|---|---|---|---|
| `hound-builder` | one scoped change from a Context/Goal/Touch/Accept/Do-NOT spec | `swa_devin` | files in `Touch:` |
| `hound-reviewer` | reads the whole diff, mechanical checks, re-runs the suite | `swa_test` | nothing |
| `hound-livefire` | runs `scripts/live_fire.py` against real Slack and Google, cleans up | live (read-only queries) | `LIVE-FIRE.md` |
| `hound-wiki-keeper` | audits docs against code; fixes docs only when told | none | docs only |

Definitions are loaded when a session starts. In the session that creates or changes one, or if the
type is reported "not found", spawn `general-purpose` and tell it to read the role file first.
Give every agent a self-contained prompt; it has none of your context. Builder runs and reviewer
runs must not overlap on the same database.

**The judge loop, every change:** builder implements, reviewer reads the entire diff and re-runs
pytest and ruff itself, then the owner-facing summary quotes the reviewer's real output. Standing
checks: no raw `select(` or `.filter_by(` bypassing `TenantScopedRepository`; no `team_id` or
`slack_user_id` in agent tool args; no `except: pass`; no external call inline in a command handler;
model change means migration; no real identifiers in tracked files. Across two sessions of agent
batches, every batch had at least one real defect its own tests missed, so the reviewer is not optional.

## 8. Gotchas

1. **Any command that calls Google, Groq or Slack's API must ack and enqueue**, never answer inline;
   Slack's budget is 3 seconds and tests do not run that clock.
2. **`session.flush()` after adding a parent row** before adding a row that references it. The models
   use plain foreign keys with no `relationship()`, so SQLAlchemy will not order the inserts.
3. **Test routes with `httpx.AsyncClient` and `ASGITransport`** (the `api_client` fixture in
   `tests/conftest.py`), never the sync `TestClient`, or asyncpg complains about a different loop.
4. **Mocked tests hid real bugs.** A missing `userinfo.email` scope passed 138 tests. Anything
   touching an external API needs one real call in `LIVE-FIRE.md`.
5. **Groq's binding limit is 8,000 tokens per minute**, not the daily quota. Keep tool schemas small.
6. **LLM routing is not deterministic** at default temperature; a mention may pick a different tool
   or answer in prose.
7. **`chat.postMessage` needs the bot to be in the channel.**
8. **Google "Testing" apps issue refresh tokens that expire in 7 days.** Publish to production first.
   A dead token surfaces as `invalid_grant` and sets `users.google_link_broken_at`.
9. **The digest exists only as the `get_digest` agent tool** (an @mention), not a `/digest` command,
   and its reply is plain text on purpose because the LLM composes it.
10. **Migrating the live DB and restarting uvicorn is a manual pair.** A restart on new code before
    the migration breaks every task query.

## 9. Deliberately not built

Hosting (Render, Supabase, cron-job.org were researched; see `docs/BUILD-LOG.md`), Google app
verification, Outlook, an observability package, rate limiting and audit logging, a scheduled
digest, and a separate worker process. The Slack scopes `users:read` and `im:history` are requested
but unused; dropping them would force a re-authorization of the installed workspace.

## 10. Where to look

| For | Read |
|---|---|
| design and diagrams | `ARCHITECTURE.md` |
| contributing rules | `CONTRIBUTING.md` |
| first-time provisioning | `RUNBOOK.md` |
| evidence of real external calls | `LIVE-FIRE.md` |
| recording the demo | `DEMO-SHOTLIST.md` |
| how and why it was built, incidents | `docs/BUILD-LOG.md` |
| Slack app config | `slack-app-manifest.yaml` |
