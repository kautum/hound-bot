# PROJECT-WIKI.md — Hound

Read this before touching the code, even in a fresh low-effort session with no memory of how
this was built. It exists so you don't have to re-scan the codebase or re-derive decisions
that were already made and paid for once. `ARCHITECTURE.md` is the design doc (read it too —
this file is the *operational* companion: what's actually done, what's a known trap, and how
to use Devin correctly). The full plan with every decision's reasoning is at
`~/.claude/plans/alright-now-lets-glistening-yeti.md`.

**The project is named "Hound"** — a Slack bot that chases down overdue tasks and sniffs out
the earliest mutual free slot on your calendar. Public repo:
**https://github.com/kautum/hound-bot** (main branch, CI green). The internal package/module
name (`app.*`, `slack-workplace-assistant` in `pyproject.toml`) hasn't been renamed to match —
that's cosmetic and low priority, not a functional gap.

**Last updated:** 2026-09-22, after Batches F–I landed on top of everything in §0.11 —
Block Kit "Book"/"Cancel" buttons on `/meet`, a friendly HTML install page + welcome DM, an
App Home tab, and a "Mark done" button on reminder DMs. See §0.12. 193 tests passing, all
committed and pushed — see §0.12 for how, since the local `git` binary is still broken.

---

## YOUR TASKS — read this first, always

**One blocker left, and it's optional:**

1. **Re-link Google Calendar — but publish first, or it just dies again.** The refresh token
   linked 2026-09-10 has actually expired (`invalid_grant`, confirmed live 2026-09-21) because
   the app was never published to production — the 7-day-Testing-mode risk this file warned
   about materialized. Order matters:
   - Google Cloud Console → OAuth consent screen / Audience → **Publish app** first
   - Then re-link via `/link-calendar` in Slack
   - This only unblocks D1's last 3 steps (real freeBusy/propose/book against Google) and the
     `/meet propose`/`book` shots in `DEMO-SHOTLIST.md`. Everything else already works.

**Git is no longer a blocker** — the local `git` binary is still broken (Xcode license), but
§0.12 documents a working `dulwich` (pure-Python git) workaround that commits and pushes over
HTTPS using the `gh` CLI's existing token, no sudo needed. **Every batch through this session
is committed and pushed** to `worktree-slack-bot-scaffold`, and **PR #1 is open with CI green**:
https://github.com/kautum/hound-bot/pull/1 — merge it (or ask to have it merged) once you've
looked it over. Hosting (Render/Supabase/cron-job.org) is explicitly deferred past "finished,"
not just parked — a demo video doesn't care whether it's recorded against localhost or a
hosted URL. `DEMO-SHOTLIST.md` covers all 10 shots including the new UI (App Home, Book/Cancel
buttons, install page) and is ready to record whenever you are.

---

## 0.75. Session 2026-09-10 — live fire started, plan rewritten, Devin build queue (D0–D8)

**Full detail and reasoning lives in `~/.claude/plans/alright-now-lets-glistening-yeti.md`,
section "PLAN OF RECORD — 2026-09-10" — that supersedes everything below it in the plan file,
including "TONIGHT'S RUN". This section is the short version.**

**The goal was reframed, and it shrinks the project.** User's own words: *"i dont need this bot
to be self hosting forever as a product, i just want it to live for a few months so i can show
it to my supervisors and put it in my resume."* Old done-criteria (a stranger installs it into
their own workspace) is replaced by: a supervisor sees it work live, for a few months, and the
repo reads well. This drops Google verification, the domain, and "any workspace" scope entirely.

**Real live fire happened, and it found a real bug 138 tests missed.**
- A real Slack workspace exists now: `Devin Bot`, `team_id=<redacted>`. The real `xoxb-` token
  was verified via `auth.test` and seeded encrypted into Postgres.
- `uvicorn` + `ngrok` running locally at `https://<redacted>.ngrok-free.dev`, `/health`
  green over the public internet, not just localhost.
- **`/link-calendar` works end to end for real**: Slack → our state-issuing endpoint → Google
  consent → our callback → `{"status":"linked","email":"<redacted>@gmail.com"}`.
- Getting there took two real fixes, both things 138 passing tests never caught because every
  test mocks the external call:
  1. **Google's Test-User allowlist.** The linking account had to be added under Cloud
     Console → Audience → Test users before Google would even show the consent screen.
  2. **A real bug: `fetch_user_email()` 401'd.** `GOOGLE_SCOPES` in `app/calendar/google_calendar.py`
     only ever requested `calendar.freebusy` and `calendar.events` — never `userinfo.email` — so
     the access token had no permission to read the linked email. Fixed by adding the scope
     (non-sensitive, no extra Google verification triggered). **This is the project's defining
     failure mode appearing again**: a passing mocked test hid a call that was never legal to make
     for real. It's why the plan's next unit (D1) exists — drive every remaining never-yet-real
     external call once, before trusting any more of the suite's green.

**New risk found by direct research, not assumption: the 7-day token bomb.** A Google OAuth app
in "Testing" status issuing *sensitive* scopes (ours) gets refresh tokens that expire in 7 days —
so the link that just started working was going to silently die next week. Fix: publish the app
to **"In production"** (no verification needed for this step) — refresh tokens become long-lived.
Costs: an unverified-app warning users click past, and a 100-user lifetime cap on that scope —
both irrelevant here. **This is now Your Task #1 above and is time-sensitive.**

**GitHub Education, checked today rather than assumed — re-derived twice after getting it wrong
once:**
- **DigitalOcean's $200 credit is gone.** Left the pack; last redemption 31 Jul 2026, credits
  expired 1 Aug 2026. This kills what would have been the obvious hosting upgrade.
- **A Heroku offer exists** (~$13/mo × 24 months) — a real fork against Render-free (never
  sleeps, vs. £0/no-card/30-60s cold start). **Deliberately not decided** — parked until after
  the build per user instruction; the fork is recorded in the plan file so it doesn't need
  re-deriving.
- **Sentry Education is free for a year** via the pack (errors, logs, and — usefully — 1 cron
  monitor). This directly undoes the earlier decision to drop Sentry for an unverified card
  requirement (see §5/§7's old notes). Queued for whenever hosting/monitoring gets decided.
- **The free domain** (Namecheap `.me`, no card; or name.com `.dev`/`.app`, requires a card) is
  now pure polish, not load-bearing — Google verification dropped out of scope, so nothing
  actually needs a domain anymore. Optional, any time.
- **Authoritative source is `education.github.com/pack` itself**, personalised to the account —
  web search results disagreed with each other and one still advertised the dead DigitalOcean
  credit.

**The plan was adversarially tested before any more code moves — two subagents, results
reconciled rather than merged blindly.** A fork (full project context) and a cold
general-purpose agent (zero context, briefed only on the stated goal) both attacked the draft
plan independently. Combined, 12 concrete defects against the actual code:

- **The most serious one, and it's a process risk, not a code risk:** `tests/conftest.py`'s
  `drop_all` runs against whatever `DATABASE_URL` is set, and `.env` — pointing at the live
  seeded database with the real linked Google token — sits in the same worktree Devin will run
  in. One missed env override and Devin's own test run destroys state that cost real human
  clicks and cannot be regenerated without them. **This is now unit D0**, a mechanical guard
  (`conftest.py` refuses to run outside `{swa_test, swa_devin}`) written by Claude, not Devin,
  before any Devin task fires.
- **A near-miss that would have shipped a bug:** the original "add a retention sweep" unit had
  no pinned cutoff. Deleting `processed_events` rows too aggressively re-opens Slack's own
  dedupe window and lets a retried event create a duplicate task. Fixed: cutoff pinned in the
  spec at 7 days (168× Slack's ~1h retry ceiling), and `oauth_states` was dropped from that unit
  entirely once checked against the code — it already self-deletes on consume.
  `oauth_state_repository.py:60-62`.
- Live fire's `/meet` step needed correcting too: the parser hard-rejects zero participants
  (`meet_command_parser.py:43-44,56-57`), and a script cannot obtain a genuine Slack
  `response_url` — that step now splits into scripted-with-a-capture-endpoint plus one
  human-typed `/meet` in real Slack.
- README/manifest/LICENSE were originally sequenced last despite being the artifacts a
  supervisor or recruiter actually opens, and the README is currently factually wrong ("112
  tests", old definition of done, links a path only the author can open). Moved up.
- A demo video/GIF had no unit at all despite being named in the definition of done — now D8.
- **Standing rule, not a one-off unit:** any Devin diff touching a repository file gets checked
  for a raw `select()`/`.filter_by()` that bypasses the tenant-scoped base class — the entire S2
  fix rests on that one convention with nothing mechanical enforcing it.

**Decision, restated because it reverses §0.5's note below: "Devin does the majority of the
remaining build, Claude is judge and instructor."** Not "Devin gets disjoint infrastructure
work" as previously decided — that changed today, on explicit instruction, because Devin credits
are otherwise unspendable for this user. The mitigation for the risk that creates (Devin touching
files that used to be off-limits, like `app/agent/tools.py`) is the review discipline in the plan
file's "Execution model" section — read every diff in full, re-run the suite here not just trust
Devin's VM, check specific invariants (the tenant-scoping grep, the agent-tools injection
boundary) rather than skim.

**The build queue, in order — D0 through D8, with owners:**

| # | Unit | Owner |
|---|---|---|
| D0 | Guard `conftest.py` against wiping the live seeded DB | **Claude** |
| D1 | Live-fire driver script for the remaining never-real calls | Devin writes; Claude runs + judges |
| D2 | Fix whatever D1 finds | Devin builds, Claude specs |
| D3 | README rewrite — currently false, moved up from last | Devin drafts, Claude rewrites |
| D4 | Save the Slack app manifest to the repo | Devin |
| D5 | S12 (`app_uninstalled` write path) + S13 (silent unknown-event-type swallow) | Devin |
| D6 | S11 — on-demand `@bot digest` tool | Devin, Claude checks the injection-boundary invariant |
| D7 | `processed_events` 7-day retention sweep only (not `oauth_states`) | Devin |
| D8 | Demo video shot list + recording | Claude scripts, user records |

Full specs, exact file:line evidence, and the ready-to-fire first Devin prompt are in the plan
file — read it before starting D0, don't reconstruct it from memory.

---

## -1. Tonight's run (2026-09-08) — read this if it's the morning after

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

**Not done that night, deliberately:** the real ngrok round trip, a real Slack post, and
S11/S12 (analytics wiring, uninstall handling — Unit 10). See below — most of the rest of
Unit 8/9 got done the next day instead.

**Devin spend that night:** one load-test task (committed), and 4/5 of a test-repair task
(the 5th finished by hand after a hard time cutoff — see the commit for what happened).

---

## 0.5. Follow-up session, 2026-09-09 — audit, live Groq call, GitHub repo, CI

**An independent Devin-run audit re-verified S1–S10 from scratch** (read the actual source,
didn't just trust PROJECT-WIKI.md's word), and ran the full suite itself: 138/138 passed. It
also flagged 10 new gaps, all operational-hardening rather than structural
(rate limiting, HTTP retry/backoff, DB pool sizing, audit logging, job cleanup, etc.) — ranked
by severity in the plan file if you want the full list. **One of its findings was checked and
found wrong**: it claimed `db.py` was missing `pool_pre_ping` — it's actually there (added in
the S9 fix). Worth remembering: audit output gets verified against the source too, not trusted
just because it came from a second model.

**A real, live Groq API call was made** (not mocked) — "remind me to file the report friday at
5pm UTC" correctly resolved to a `create_task` tool call with `due_at_utc: 2026-09-10T17:00:00Z`,
proving the model can do relative-date math against a stated "today." Confirms the one external
LLM contract most likely to silently break.

**GitHub repo created and CI is green**, twice: `https://github.com/kautum/hound-bot`, pushed
from this worktree's branch as `main`. First run (the full existing test suite, migrations,
lint) passed clean on the first try. A second commit added an `alembic check` CI step
(`.github/workflows/ci.yml`) as a fast-failing companion to
`tests/test_migrations_match_models.py`'s in-suite drift check — also green.

**Devin model access, confirmed directly, not assumed:** on this account, **any** explicit
`--model` flag — tested across every vendor (GPT-6 Astra, Claude Opus 5, Claude Sonnet 4.6,
GPT-5.3-Codex, GLM-5.3, SWE-1.6) — returns an instant `Error: Upgrade to Pro to access this
model`. This is **not brand-specific**; it's a blanket gate on explicit model selection. **Only
the CLI's default model (no `--model` flag at all) is usable**, and it's what did all of
tonight's and today's real Devin work. Don't spend more credits testing other model names —
the gate is structural. Decision going forward (2026-09-09): the user doesn't want a Pro
upgrade, so **Devin does a minority of the remaining build** (well-scoped, disjoint,
machine-verifiable tasks it's naturally suited for), and Claude does the majority directly,
acting as Devin's reviewer/judge on whatever it's given.

**A GitHub repo-creation attempt was blocked once by Claude Code's own safety classifier**
(different from anything Devin- or model-related) — creating a new public repo and pushing
needs the user's live, in-the-moment go-ahead in the conversation; it can't be pre-authorized
by an earlier "yes" a few turns back. Once the user re-confirmed in the same turn, it went
through cleanly. Worth knowing if this needs doing again on some other repo.

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

## 0.9. Session 2026-09-21 — git broke, token expired, plan compressed to finish faster

**What happened since 2026-09-17:** the machine sat idle for 4 days. When work resumed, git
itself was broken system-wide (`sudo xcodebuild -license accept` never got run after an apparent
Xcode/macOS update), and the Google refresh token — never published to production despite the
warning above being written 11 days earlier — had actually expired. Neither is a code bug; both
are exactly the two things flagged as risks and neither got closed out in time. Recorded plainly,
not glossed over: **a live-fire finding that gets written down but not acted on is the same as
not finding it.**

**What's confirmed done and verified, unaffected by either blocker:**
- D0 (DB safety guard), D0.5 (CI fix, confirmed green on GitHub), D0.6 (environment resilience —
  Postgres now runs via `brew services`, survives reboots)
- D1 steps 1-2 (`/task add`, reminder tick) — both verified against the real DB, real reminders
  actually sent as Slack DMs
- D4 (Slack app manifest) — committed; caught and fixed an invalid-YAML bug from Devin's first
  draft before it landed (an unquoted `@`-prefixed string, which YAML's parser rejects)
- D5 (S12 `app_uninstalled` handling + S13 stopped silently marking unknown `event_type` jobs as
  done) — fully built and test-verified (140/140 passing, lint clean) but **stuck uncommitted**
  until git works again

**The plan was compressed, not abandoned**, per explicit instruction: *"the majority of the build
is by devin, u r just the brain and orchestrator... has taken too much time... let you build."*
Concretely: the remaining low-risk units (D6 digest tool, D7 retention sweep, D3 README/LICENSE)
get batched into two larger Devin dispatches instead of one-unit-at-a-time, while anything
touching live data (D7's migration against the real seeded DB) or requiring real external calls
(D1's remaining steps) stays exactly as careful as before. Three decisions that would previously
have been asked as questions got decided instead, since none are load-bearing enough to justify
another round trip: hosting deferred past "finished" (a demo video doesn't care if it's local or
hosted), public-doc identifier redaction (keep the narrative, scrub the real email/team_id/ngrok
host), and LICENSE = MIT. Full detail: `~/.claude/plans/alright-now-lets-glistening-yeti.md`,
section "PLAN OF RECORD — 2026-09-21".

---

## 0.10. Same-day continuation, 2026-09-21 — Batch A/B landed, a real incident, D1 steps 1-2 done

**Git is still broken** (`sudo xcodebuild -license accept` — even `brew install git` needs it,
there's no way around it without your password) — nothing is committed yet, everything below
is real, verified, and sitting on disk uncommitted.

**Batch A (D6+D7) and Batch B (D3) both landed and are verified**, per the compressed plan:
- `get_digest` registered as a 4th agent tool — zero-arg, read-only, injection boundary intact
  (a real cross-tenant test proves it, not a tautology). Caught one real bug in Devin's own
  work before accepting it: `/internal/tick`'s new retention sweep never called
  `session.commit()`, so the delete would flush-then-rollback and silently no-op in production
  outside of tests that commit manually themselves — fixed with a one-line `await
  session.commit()` in `app/api/routes_internal.py`.
- `processed_events` 7-day retention: migration `0008` (index + one-time cleanup), a recurring
  sweep wired into `/internal/tick`, model/migration drift check passing. Applied to the live
  database (see incident below — it landed there by accident before I could apply it
  deliberately, but the schema state itself is correct).
- README restructured (old "stranger installs it" goal removed, dead `~/.claude/plans/...`
  link removed, "Devin's role" section rewritten with real specifics instead of vague prose),
  LICENSE added (MIT), `PRIVACY.md` replaced with an honest one-paragraph stub — no more
  `[BRACKETS]`. Test count filled in as 145 (real, `pytest --collect-only` fresh).
- 145/145 tests passing, ruff clean, throughout.

**A real incident happened, and it's fully recovered — recorded plainly, not glossed over.**
Batch A's spec asked Devin to prove migration `0008` upgrades and downgrades cleanly, and told
it to "use whatever `DATABASE_URL` is already in the environment" — `.env` in this worktree
defaults to the **real live database**. `tests/conftest.py`'s D0 guard only intercepts
pytest's `drop_all`; it does nothing to stop a bare `alembic downgrade base` / `upgrade head`
run directly. Devin's verification step ran exactly that against the live DB. Result: the real
`workspaces`, `users`, `tasks`, and every other domain table were wiped; `alembic_version` was
left at the new head, `0008`.

**Recovered, not just patched around:**
1. Found an untouched `pg_dump` backup from 2026-09-10 (D0's original setup) still on disk.
   Extracted just the `workspaces` and `users` rows (schemas matched exactly, no reshaping
   needed) and inserted them directly into the live DB — the real encrypted bot token and the
   real encrypted Google refresh token (already known-expired, so nothing new lost there) are
   back. Everything else that was wiped (tasks, reminders, meetings, queue rows) was always
   disposable demo/test data with no irreplaceable state.
2. **Closed the actual hole**, not just the symptom: `alembic/env.py` now carries the same
   guard shape `conftest.py` already had — any `alembic` invocation against a database name
   outside `{swa_test, swa_devin, swa_migration_check}` refuses to run unless
   `CONFIRM_LIVE_ALEMBIC=<db name>` is set explicitly for that one call. Verified: an
   unconfirmed run against the live DB name raises immediately with a clear message; a
   confirmed one, and any run against the test databases, proceed normally. 145/145 tests
   still pass with this in place.
3. This was my own spec-writing mistake, not a Devin overstep — noted so the lesson is "write
   tighter specs for anything that could touch a real database," not "don't trust Devin more."

**D1 steps 1-2 driven for real again, post-recovery, with real evidence in `LIVE-FIRE.md`:**
signed `/task add` → real 200, real task row confirmed in Postgres; `/internal/tick` → real
reminder DM, `sent_at` confirmed set on the real row. Also drove the new `get_digest` tool live
through a real Groq call (not part of the original 5-step plan, but the environment was already
up) — confirmed correct output matching the DB exactly, and incidentally found two genuine,
non-bug Slack/LLM characteristics worth knowing about (channel membership requirement for
`chat.postMessage`; LLM tool-routing isn't perfectly deterministic at default temperature) — both
written up in `LIVE-FIRE.md` rather than treated as defects to chase.

**D1 steps 3-5 still blocked** on the Google publish-then-relink action above — nothing changed
there, still needs you.

**D8 shot list written** (`DEMO-SHOTLIST.md`) — ready for you to record whenever, doesn't wait
on Google (the `/meet` shots are marked optional/skippable if calendar linking isn't back yet).

**What's left, in order:** you fix git and Google; I commit everything above the moment git
works; then D1 steps 3-5; then D3.6 (re-verify the test count hasn't drifted); then you record
D8. Hosting remains explicitly deferred, not a gate on "finished."

---

## 0.11. Same-day continuation, 2026-09-22 — four new features, Devin-built, Claude-judged

Per explicit instruction to keep building without stopping and add more real functionality,
three Devin dispatches landed on top of §0.10's state, each fully verified (each batch's own
test count shown; final count below). **Still uncommitted — git is still broken.**

**Batch C — task reassignment + meeting cancellation (159 tests, +14):**
- `/task reassign <task-id> @newassignee` — `reassign_task()` mirrors `mark_task_done`'s exact
  authorization shape (assignee or creator only). No reminder-table changes needed: the
  scheduler already reads `task.assignee_slack_id` fresh at send-time, so a reassignment is
  picked up automatically.
- `/meet cancel <meeting-id>` completes the propose→book→cancel lifecycle (only propose/book
  existed before). Added `cancel_event()` to the `CalendarProvider` interface and Google's
  implementation (a real `events.delete` call, respx-verified at the HTTP layer). Cancelling a
  merely-proposed meeting makes zero Google calls; cancelling a booked one actually deletes the
  real calendar event before marking it cancelled in our DB — verified with a fake-provider
  call-count assertion, not just a status check.

**Batch D — recurring tasks (170 tests, +11 net after a fix):**
- `/task add @user Title | <due> | repeat:7` — a nullable `recurrence_interval_days` column
  (migration `0009`, chained after `0008`). Completing a recurring task automatically spawns
  the next occurrence, same interval carried forward (proven by chaining a third occurrence,
  not just a second).
- **A real regression caught in review, not by Devin's own tests**: the parser switched from
  `rpartition` (splits on the *last* `|` only) to a rigid `split("|")` with a hard 3-segment
  cap, which broke any task title containing a literal `|` character (previously worked fine,
  since the old code always treated the last pipe as the delimiter). Fixed back to
  `rpartition`-based parsing while keeping the new `repeat:N` segment, added a dedicated
  regression test, and corrected one of Devin's own tests that had encoded the buggy
  three-segment-cap behavior as if it were correct.
- Schema-migration safety note: this dispatch's own acceptance criteria required running
  `alembic upgrade`/`downgrade` against `swa_test` — the hardened guard from §0.10 (added
  after the earlier incident) now makes it structurally impossible for a dispatch like this to
  accidentally hit the live database, confirm-flag or not.

**Batch E — Block Kit UI + `/slack/interactions` (177 tests, +7):**
- `/task list` now renders Slack Block Kit (a section block per task + a "Mark done" button)
  instead of plain text — closes the README's own long-standing "Block Kit UI: not built"
  admission.
- New endpoint `POST /slack/interactions`, signature-verified over the raw body exactly like
  the other two inbound endpoints (`/slack/commands`, `/slack/events`) — treated as
  security-sensitive by design, since it's a new attack surface that triggers a real state
  change. A button click re-runs the *exact same* `task_service.mark_task_done` authorization
  check the `/task done` slash command already uses — no duplicated logic, no new bypass.
  Tests prove a tampered signature 401s **and leaves the database untouched**, which is the
  one test in this batch that actually matters most.
- **A real, explicit rule violation caught in review**: `_send_slack_response`'s helper used
  `except Exception: pass` — this project's own `ENGINEERING.md` and `CONTRIBUTING.md` both
  explicitly ban `except: pass` as a non-negotiable rule, and this dispatch's code violated it
  outright despite Devin having read both files first. Fixed to log the failure
  (`logger.warning(..., exc_info=True)`) instead of swallowing it silently, and switched the
  fire-and-forget call from a bare synchronous `httpx.post()` to the same
  `async with httpx.AsyncClient(timeout=10.0)` pattern every other response_url reply in this
  codebase already uses, for consistency. Re-verified all tests still pass after the fix
  (`BackgroundTasks` runs async callables natively — no call-site changes needed).

**Docs updated to match:** `README.md` (feature list, phase table, test count → 177),
`DEMO-SHOTLIST.md` (new shots for the button click, recurrence, and cancellation).

**Pattern worth naming plainly:** every single batch in this session — Batch A/B (§0.10) and
C/D/E here — had at least one real, non-trivial defect that Devin's own "tests pass" claim did
not surface, and that only turned up because every diff was read in full before being accepted,
not skimmed. The judge-loop is not ceremony; it has now caught 8 distinct real bugs across two
sessions of Devin dispatches, and the pattern is consistent: greenfield feature code from an
agent still needs a human (or Claude, playing that role) to check assumptions the agent's own
tests don't think to question.

---

## 0.12. Same-day continuation, 2026-09-22 — Batches F–I, the git binary worked around, PR open

Explicit instruction this session: finish the remaining UX gaps identified in the prior
self-review, treat Claude as the sole orchestrator ("director"), and end the session with a
git-committed, GitHub-visible, demo-ready product with no complications for the user to
resolve. Four more Devin batches landed, each judged the same way as every prior batch —
full diff read, tests re-run locally against `swa_test`, never trusting Devin's own claim.

**Batch F — Block Kit "Book"/"Cancel" buttons on `/meet`.** `handle_meet_propose` now
attaches a "Book" button (action_id `meet_book`) to its response; a successful `/meet book`
attaches a "Cancel" button in its place. `/slack/interactions` dispatches both action_ids by
enqueueing the same `meet_book`/`meet_cancel` jobs the slash-command path already used — the
booking/cancelling itself still happens in the worker, never inline, because both call
Google's API and must stay under Slack's 3-second budget. `meet_cancel` clicks are checked
against `meeting.organiser_slack_id` inline before enqueueing (a non-organiser gets an
immediate rejection, no job created); `meet_book` clicks are allowed to enqueue from anyone
because the worker's `meeting_service.confirm_meeting` already enforces organiser-only
booking and will reject a non-organiser's booking attempt after the fact — this mirrors the
exact asymmetry the pre-existing slash-command path already had, not a new hole. Verified:
184/184 tests, ruff clean. **Also found and fixed directly (not delegated): the Slack app
manifest never got an `interactivity` block after Batch E added `/slack/interactions` in the
prior session** — without it, no Block Kit button (old or new) actually works against real
Slack. Added `settings.interactivity` to `slack-app-manifest.yaml`.

**Batch G — friendly install flow.** `GET /slack/oauth/callback` now returns a real HTML
success page (inline CSS, no external assets) instead of a bare JSON blob, and — using
Slack's `authed_user.id` field from the OAuth exchange, which Slack returns without any
extra scope — sends a best-effort welcome DM listing the available commands. DM failure or a
missing `authed_user` is logged and never blocks the install (broad `except Exception`, but
logged with `exc_info=True`, not swallowed). Verified: 188/188 tests, ruff clean.

**Batch H — App Home tab.** New `app_home_opened` handling: `routes_events.py` now enqueues
it like every other event (never handled inline), `worker.py`'s `handle_app_home_opened`
looks up the viewer's own open tasks (`list_open_tasks`, correctly scoped by `job.team_id`)
and calls `views_publish`. `app/ui/blocks.py` factored the existing per-task
section+button block into a shared `_build_task_block` helper, reused by both the task-list
view and the new `build_app_home_view`. Manifest updated with `features.app_home` and the
new event subscription. Verified: 192/192 tests, ruff clean, including a tenant-isolation
test that creates tasks in two teams and asserts only the viewer's own team's tasks appear.

**Batch I — reminder DMs get the same "Mark done" button.** `app/scheduler.py`'s
`process_due_reminders` now passes `blocks=_build_task_block(task)` alongside the existing
`text=` fallback on its `chat_postMessage` call — no change to escalation logic, recipients,
or authorization; clicking the button goes through the exact same `/slack/interactions`
`task_done` path as everywhere else. Verified: 193/193 tests, ruff clean.

**The git binary is still broken (Xcode license, needs a `sudo` password nobody's typed in),
and there's still no path to fix it without that password — but it no longer matters.**
`pip install --user dulwich` (a pure-Python git implementation) opens this exact repo/worktree
directly, with zero dependency on the system `git` binary or Xcode. `dulwich.porcelain.add`,
`.commit`, and `.push` (the last one authenticated over HTTPS using `gh auth token` — the
`gh` CLI was already logged in) all work correctly against a live GitHub remote. Three real
commits landed this session (feature batches, then the scheduler batch that was mid-flight
during the first commit, then a docs pass), each pushed and each triggering a green CI run
before the next one. **`gh pr create` still shells out to local `git` and fails** — worked
around by calling `gh api repos/kautum/hound-bot/pulls` directly instead. **PR #1 is open,
CI green, not yet merged:** https://github.com/kautum/hound-bot/pull/1 — merging it was left
for the user to actually look at first rather than auto-merged, even under the "no need for
approval" blanket instruction, because merging to `main` is the one step here that's genuinely
hard to reverse cleanly on a public repo.

**Not done this session, and why:** digest replies were deliberately *not* converted to
Block Kit. The digest's final text is composed by the LLM on top of the `get_digest` tool's
raw string return (see §0.11's LLM tool-routing non-determinism note) — forcing structured
blocks through that path would mean either post-processing the model's freeform reply
(fragile) or reworking the agent loop to special-case one tool's output shape (disproportionate
complexity for a portfolio demo). Kept as plain text on purpose, not an oversight.

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

### The delegation boundary reversed on 2026-09-10 — read this, not the paragraph you remember

**Old rule (through 2026-09-09): Devin gets disjoint infrastructure work only; the security-
sensitive core (`app/agent/tools.py`, `app/services/`, `app/repositories/`, `app/core/security.py`,
`alembic/`) was never delegated.** That rule is **overridden by explicit user instruction**
2026-09-10: *"devin does all the building, using the credits... u just be the judge and
instructor."* Devin credits are otherwise unspendable for this user, so this is the user's
deliberate tradeoff to make, not an oversight.

**What replaces the old file-based exclusion list: a review discipline, applied to every diff
regardless of which files it touches.** Full detail in the plan file's "Execution model —
Devin builds, Claude judges" section. The load-bearing pieces:

1. **D0 exists specifically because of this reversal** — a mechanical guard in `conftest.py` so
   a Devin test run can never `drop_all` the live seeded database (real Slack token, real linked
   Google refresh token) no matter what `DATABASE_URL` ends up being. Written by Claude, before
   any Devin task fires. Do not skip it just because it looks like process overhead.
2. **Two specific, mechanical checks, not "read carefully":**
   - Any diff touching a repository file: grep it for a raw `select()`/`.filter_by()` that
     bypasses the tenant-scoped base class. The entire S2 tenant-isolation fix rests on that one
     convention (`TenantScopedRepository`) with nothing enforcing it structurally.
   - Any diff touching `app/agent/tools.py`: confirm `team_id`/`slack_user_id` still come only
     from `AgentContext`, never from a model-supplied argument. That file's own docstring names
     this as the injection boundary.
3. **Re-run the full suite here, against `swa_test`, never trust "tests pass in Devin's VM"
   as evidence.** Precedent: Devin's CI PR looked correct on review-by-glance and shipped a
   wrong database driver (`postgresql://` instead of `postgresql+asyncpg://`) that would have
   broken every CI run.
4. **Reject and re-instruct rather than quietly patch.** A silent fix teaches Devin nothing
   about what was wrong and hides how good the actual output was.

**Current queue (D0–D8, full specs in the plan file)** is the concrete application of this —
D0 is the guard itself; D1 is Devin-written but Claude-run (it needs real credentials this
machine holds, not Devin's VM); D6 (agent tool) gets the specific injection-boundary check above
on top of a normal read.

**The invocation and spec template above are unchanged.** What changed is scope, not mechanics.

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
   migration checks** (`make db-up` creates `swa_test` against your native local Postgres for
   exactly this; don't share it with a database you're also using for something you want to
   persist). There is no docker-compose in this project — Docker was never installed on the
   dev machine (see the 2026-09-05 Part 0 findings); Postgres runs natively via `brew services`.

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
make db-up       # native local Postgres, creates the app + swa_test databases
make migrate     # alembic upgrade head
make test        # pytest — self-provisions schema per test, doesn't need `make migrate` first
make lint        # ruff check app tests
make run         # uvicorn, hot reload
```

If Docker isn't available (it wasn't in the environment this was built in), a native
`postgres` install works identically — `initdb`, `pg_ctl start -o "-p 5433 -k /tmp"`, then
point `DATABASE_URL` at `postgresql+asyncpg://postgres@/dbname?host=/tmp&port=5433`. Don't
share that instance between manual `alembic` runs and `pytest` runs — see gotcha #4.
