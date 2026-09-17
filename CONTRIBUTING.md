# Contributing

Read `ARCHITECTURE.md` first — always. This file covers how tasks get scoped and what the
rules are while working in this repo.

## If you're an autonomous agent (Devin) picking up a task

You start every session with no memory of the last one. `ARCHITECTURE.md` exists to carry the
context a human teammate would otherwise give you verbally — read it before writing anything.

Every task you're given follows this shape. If a task you receive is missing one of these
sections, ask for it rather than guessing:

```
Context:   Read ARCHITECTURE.md first.
Goal:      <one sentence>
Touch:     <explicit file/directory list>
Accept:    <machine-checkable — e.g. `pytest tests/scheduling` passes, `/health` returns 200>
Do NOT:    touch app/core/, change the schema, or add dependencies without noting why
```

**Stay inside the Infrastructure track** (`tests/`, `.github/`, `app/ui/blocks/`,
`app/observability/`, `Dockerfile`, `render.yaml`) unless a task explicitly says otherwise. The
Domain track (`app/core/`, `app/models/`, `app/repositories/`, `app/services/`, `app/agent/`,
`alembic/`) is scoped separately, on purpose, so parallel work doesn't merge-conflict.

**Never touch the schema or migrations** without an explicit task asking for it. The database
is the highest-blast-radius part of this project — tenant isolation depends on every table
being shaped correctly, and a schema change made without full context is the single most
expensive kind of mistake here.

**Never add a dependency without saying why in the PR description.** This is a zero-budget
project — a new dependency that pulls in a paid SaaS client, or that meaningfully changes the
free-tier math in `ARCHITECTURE.md`, needs a human to sign off before it lands.

## Non-negotiable rules, regardless of who's writing the code

- **`team_id` isolation is absolute.** Every query touching a tenant-scoped table goes through
  the repository layer in `app/repositories/`, never a raw query with a hand-written filter.
- **No hardcoded secrets.** Environment variables only, loaded through `app/core/config.py`.
- **Never `except: pass`.** Handle the error or let it propagate with a clear message.
- **Tokens (Slack bot tokens, Google refresh tokens) never appear in logs**, including
  exception tracebacks.
- **Fail loud, not plausible.** A parser, validator, or OAuth callback that gets bad input
  should raise, not return a plausible-looking wrong answer.

## Local setup

```
make db-up      # requires a native local Postgres already running; creates the databases
make migrate    # applies Alembic migrations
make test       # runs the test suite
make lint       # ruff check
make run        # starts the FastAPI app with hot reload
```

Copy `.env.example` to `.env` and fill in credentials as each phase needs them — the test suite
for Phase 1 (schema, security primitives) needs none of Slack/Google/Groq.
