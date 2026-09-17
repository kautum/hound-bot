# Slack Workplace Assistant

A multi-tenant Slack bot: task/deadline tracking with automated chasing, and meeting
scheduling that reads participants' Google Calendars to find the earliest mutual free
slot. Every external service in the stack is free-tier — see `ARCHITECTURE.md`.

## Status

**All seven phases of the code are built and tested (112 tests, all passing against real
Postgres).** Read `PROJECT-WIKI.md` before changing anything — it has the module map, the
Devin usage guide, and the specific bugs three review passes found (each one caught something
a passing test suite had been hiding). What's *not* built at all: Block Kit UI (everything is
plain text), observability, timeouts/backoff, and load testing. What's not *done* yet: the
accounts, deployment, and Google verification — none of that can happen without live
credentials only you can create. See `RUNBOOK.md` for exactly what's left and in what order.

| Phase | What it covers |
|---|---|
| 1 — Foundation | Schema, security primitives (HMAC verify, token encryption, tenant isolation) |
| 2 — Multi-tenant install | Slack OAuth, signed event ingestion, dedupe, job queue |
| 3 — Tasks & reminders | CRUD, `/task` command, escalation ladder, scheduler tick |
| 4 — Calendar & scheduling | `CalendarProvider` interface, Google implementation, availability intersection, meeting booking |
| 5 — LLM layer | Groq-backed agent loop over a whitelisted, Pydantic-validated tool registry |
| 6 — Analytics | Task completion rate, overdue count, weekly digest |
| 7 — Hardening | Dockerfile, Render config, CI (see below) |

## Definition of done

> A stranger can install the bot into their own Slack workspace and use tasks and
> reminders. A test account we've allowlisted can link a calendar and book a meeting.
> Google verification is submitted, and this README states its actual status.

Calendar linking opens to strangers once Google's app verification clears — that's a
real, separate gate on Google's timeline, not a formality. See `ARCHITECTURE.md`'s OAuth
flows section.

**Verification status:** not yet submitted — it needs a demo video of the working app,
which needs a live deployment first.

## Local setup

```
cp .env.example .env   # fill in credentials as each phase needs them
make db-up              # checks for a local native Postgres, creates the app + test databases
make migrate
make test
make lint
make run
```

## Devin's role in this build

CI (`.github/workflows/ci.yml`) was delegated to Devin — a real, tightly-scoped task,
not a demo. It stayed exactly within its file boundaries; one small bug (a wrong DB
driver in the workflow's `DATABASE_URL`) was caught on review and fixed directly rather
than sent back for a second billed round trip.

## Full spec

The complete architecture, security model, and phase-by-phase build plan live at
`~/.claude/plans/alright-now-lets-glistening-yeti.md`. `ARCHITECTURE.md` in this repo is
the condensed, code-facing version — read that one first if you're picking up the code.
**`PROJECT-WIKI.md` is the operational companion**: module map, known gotchas, and exactly
how to use Devin for what's left. Read it before `ARCHITECTURE.md` if you're resuming work
rather than reading for the first time.
