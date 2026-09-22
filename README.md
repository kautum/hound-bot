# Hound — A Multi-Tenant Slack Bot

A portfolio project demonstrating a production-grade multi-tenant Slack bot with:
- Multi-tenant OAuth (Slack + Google Calendar)
- Event-driven architecture with job queue
- LLM tool-calling (Groq) for natural-language task parsing
- Google Calendar integration for meeting scheduling, including cancellation
- Recurring tasks and a Block Kit interactive UI

All external services are free-tier. See `ARCHITECTURE.md` for the full system design.

## Status

**All seven phases of the code are built and tested (177 tests, all passing against real Postgres).** Read `PROJECT-WIKI.md` before changing anything — it has the module map, the Devin usage guide, and the specific bugs several review passes found (each one caught something a passing test suite had been hiding). What's *not* built: observability, timeouts/backoff, and load testing.

| Phase | What it covers |
|---|---|
| 1 — Foundation | Schema, security primitives (HMAC verify, token encryption, tenant isolation) |
| 2 — Multi-tenant install | Slack OAuth, signed event ingestion, dedupe, job queue |
| 3 — Tasks & reminders | CRUD, `/task` command (add/list/done/reassign/recurring), escalation ladder, scheduler tick |
| 4 — Calendar & scheduling | `CalendarProvider` interface, Google implementation, availability intersection, meeting propose/book/cancel |
| 5 — LLM layer | Groq-backed agent loop over a whitelisted, Pydantic-validated tool registry, including an on-demand digest tool |
| 6 — Analytics | Task completion rate, overdue count, on-demand weekly digest (`@bot digest` or `get_digest` tool) |
| 7 — Hardening | Dockerfile, Render config, CI (see below) |
| 8 — UI | Block Kit buttons on `/task list` ("Mark done"), backed by a signed `/slack/interactions` endpoint |

## Features

- **Tasks**: `/task add @assignee Title | <due date>` (optionally `| repeat:N` for a task that
  auto-recreates itself N days after each completion), `/task list` (with a one-click "Mark
  done" button), `/task done <id>`, `/task reassign <id> @newassignee`.
- **Meetings**: `/meet @user1 @user2 <minutes> | <window start> | <window end>` proposes the
  earliest mutual free slot across everyone's linked Google Calendar; `/meet book <id>` confirms
  it as a real calendar event; `/meet cancel <id>` cancels it — deleting the real Google event if
  it was already booked, or just retracting the proposal if it wasn't.
- **Natural language**: `@Hound <anything>` routes through a Groq-backed agent that can create
  tasks, list them, propose meetings, or produce an on-demand digest — the model only ever picks
  from a fixed, Pydantic-validated tool whitelist; it never touches the database directly.
- **Reminders**: automatic escalating DMs (upcoming → due today → overdue, with the overdue tier
  also notifying whoever created the task) — durable across restarts, deduplicated against
  Slack's own retry behavior.

## Hosting

This is a personal portfolio demo, not a public-facing product. Hosting is currently local + ngrok, by design — the bot needs to survive for a few months to demonstrate the implementation, not serve the public at scale. Google Calendar verification and public distribution are explicitly out of scope.

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

Most of the implementation was built by Devin (Cognition's autonomous coding agent), dispatched with tightly-scoped specs — CI configuration, the live-fire integration driver, the Slack app manifest, the on-demand digest tool, the retention sweep, task reassignment, meeting cancellation, recurring tasks, and the Block Kit interactive UI — with every diff reviewed line-by-line before it landed, against real test runs rather than Devin's own claims. That review loop has caught several real bugs before they shipped: a signature-encoding mismatch in a test script, invalid YAML in the Slack manifest, a missing `commit()` that would have made a retention sweep silently no-op in production, a parser regression that broke task titles containing a literal `|`, and a silently-swallowed exception (`except: pass`) in a new endpoint that violated this project's own stated rule against it. `PROJECT-WIKI.md` has the full account.
