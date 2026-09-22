## Live-fire steps 1-2, run for real — 2026-09-21

Ran directly against the running app over the public ngrok tunnel, real Slack workspace
`T0C077J6873`, real user `U0C059EQ4UA`, real Postgres. Real response bodies below, not
summarized.

**Before this could run, a real incident happened and was fixed** — recorded here rather
than silently absorbed into a clean-looking result, per this file's own standing rule.
A Devin dispatch (Batch A, D6+D7) included an acceptance check that ran
`alembic downgrade base` / `alembic upgrade head` to prove the new migration reverses
cleanly. Its spec told it to "use whatever `DATABASE_URL` is already in the environment,"
and `.env` in this worktree defaults to the real live database — the D0 guard in
`tests/conftest.py` only intercepts pytest's own `drop_all`, not a bare `alembic` CLI
invocation, so nothing stopped it. Result: the live `workspaces`, `users`, `tasks`, and
every other domain table were wiped; `alembic_version` was left at `0008` (the new head).
**Recovered** from a `pg_dump` backup taken back on 2026-09-10 (found still sitting on
disk from D0's original setup) — the real workspace row (encrypted bot token) and the
real linked-user row (encrypted Google refresh token, and `google_email`) were re-inserted
directly, schemas matched exactly so no data was reshaped. Everything else that was wiped
(tasks, reminders, meetings, queue rows) was disposable test data with no irreplaceable
state. **Fixed properly, not just patched around**: `alembic/env.py` now carries the same
guard shape as `conftest.py` — any `alembic` invocation against a database name outside
`{swa_test, swa_devin}` refuses to run unless `CONFIRM_LIVE_ALEMBIC=<db name>` is set
explicitly for that one invocation. Verified: an unconfirmed run against the live DB name
raises immediately; a confirmed one, and a run against `swa_test`, both proceed normally.

### Step 1 — signed `/task add` through the public URL

```
POST https://<redacted>.ngrok-free.dev/slack/commands
Response status: 200
Response body:
{
  "response_type": "ephemeral",
  "text": "Created task \"Test task from live fire\", due 2026-09-21T21:28:13.390167+00:00."
}
```

Confirmed in Postgres: task `b4eee6fb-6801-4619-913e-b13a166518b4`, "Test task from live
fire", `due_at_utc = 2026-09-21T21:28:13.390167+00:00` — the row genuinely exists, not
just a 200 from the endpoint.

### Step 2 — internal tick fires a real reminder DM

First tick ran a few seconds before the reminder's `fire_at_utc` and correctly returned
`{"reminders_sent": 0}` — not a bug, the reminder simply wasn't due yet. Waited 15s and
ran it again:

```
POST https://<redacted>.ngrok-free.dev/internal/tick
Response status: 200
Response body:
{
  "reminders_sent": 1
}
```

Confirmed in Postgres: reminder `55a30440-d7c6-4dc0-ae71-788957358872` (escalation level
1, for the task above) now has `sent_at = 2026-09-22 02:58:26.986927+05:30` — the DM was
actually dispatched to the real Slack user via the real bot token, not just counted.

### Bonus — the new `get_digest` agent tool, driven live via a real Groq call

Not part of the original 5-step plan (the digest tool didn't exist yet when that plan was
written) but verified the same way, since the environment was already up: a real
`app_mention` event with text "digest", real Groq call, real tool execution.

```
TOOL CALLED: get_digest({}) -> 'Weekly digest\n- Task completion rate: 0%\n- Overdue tasks: 1\n- Meetings this week: 0'
FINAL REPLY: Here's your weekly digest:
- Task completion rate: 0%
- Overdue tasks: 1
- Meetings scheduled this week: 0
```

Matches the real DB state exactly (1 open, overdue task; 0% completion since nothing's
been marked done). **Two real, non-bug findings from this run, worth recording:**
1. Replying to a mention in a real Slack **channel** the bot was never invited to fails
   with `not_in_channel` — correct Slack behavior, not a defect. DMs work regardless
   (that's how reminders already succeed) since `im:write` doesn't need channel
   membership. The demo video (D8) either invites the bot to the demo channel first, or
   demos the digest via DM.
2. The same input ("digest") produced `get_digest` in two isolated direct tests but
   appeared to route to `list_tasks` in one earlier real-Slack run (the reply read like a
   task list, not the digest's fixed format) — LLM tool routing is not perfectly
   deterministic at the model's default temperature. `get_digest` itself is correct and
   independently unit-tested; this is a known characteristic of an LLM-routed layer, not
   a code defect, and is already the kind of ceiling the README documents for the LLM
   layer generally.

### Steps 3-5 — still blocked

Google freeBusy (step 3), `/meet propose` (step 4), and `/meet book` (step 5) all need a
live Google Calendar refresh token. The token linked 2026-09-10 is confirmed expired
(`invalid_grant`) because the OAuth app was never published to production during its
7-day Testing-mode window — see `PROJECT-WIKI.md`'s current blockers section. These three
steps run the moment the app is published and the calendar is re-linked; nothing else is
in their way.

---

# Live-fire verification — 2026-09-08

Run under a strict one-hour time budget (session usage limit), so this covers the
highest-value checks from the plan's Unit 8, not the full ngrok round trip. What's here
was actually executed and its real output is below — nothing summarized or assumed.

## What was verified

### 1. Our Slack HMAC implementation vs. `slack_sdk`'s own `SignatureVerifier`

The single most valuable oracle in the plan: Slack's signature is the only thing standing
between the public internet and this app, so it's checked against an implementation we
did not write, not just our own test suite.

```
slack_sdk generated sig: v0=e2045badd38ceff7cf330fb7a5e0e23af35fbaab4daa8db9dd95cc8db190e985
our verify_slack_signature accepts it: True
our verify rejects tampered sig: True
slack_sdk rejects tampered sig: True
slack_sdk accepts OUR generated signature: True
ALL HMAC CROSS-VALIDATION CHECKS PASSED
```

Both directions hold: our verifier accepts a signature `slack_sdk` generated, `slack_sdk`
accepts a signature we generated, and both independently reject a one-byte-tampered
signature. This is a stronger check than `tests/test_security.py`, which only tests our
own implementation against itself.

### 2. The app imports and constructs cleanly

```
App imported and constructed OK
```

`app.main.app` builds successfully against the real (migrated) `slack_workplace_assistant`
database with all five routers included and the lifespan worker-start logic intact.

## What was NOT run tonight, and why

- **ngrok + a real signed request from outside the machine** — not started, to stay inside
  the time budget. The HMAC check above verifies the exact same code path
  (`verify_slack_signature`) that endpoint uses; what's untested tonight is FastAPI's
  routing and the raw-body-read timing under a real network hop, not the cryptographic
  logic itself.
- **A real Groq call** — not run tonight. `GROQ_API_KEY` was confirmed live and reachable
  in the prior session (see the plan file's "Verified live tonight" section from
  2026-09-08 01:xx), but re-running the actual tool-calling round trip needs Unit 8's full
  worker-loop setup, which didn't fit this hour.
- **A real Slack post** — no `xoxb-` token has been pasted into `.env` yet; this was always
  deferred to the morning per the plan's "Nothing is asked of you before you sleep" section.

## Bottom line

The one check that most needed an outside oracle — the security boundary between the
public internet and this app — passed cleanly against an independent implementation. The
remaining live-fire steps (real ngrok round trip, real Groq call, real Slack post) are
mechanical once the three morning login steps are done; nothing in tonight's fixes changed
their shape.

---

## Live-fire driver script (2026-09-10)

A comprehensive live-fire driver script now exists at `scripts/live_fire.py`. This script
was created to drive each real external call once and print the ACTUAL response body,
because mocks hid a real bug (the userinfo.email scope miss).

### Usage

```bash
# Print plan without making network calls
python scripts/live_fire.py --dry-run

# Run a specific step only
python scripts/live_fire.py --step 1

# Run all steps
python scripts/live_fire.py
```

### What the script tests

1. **Signed /task add through public URL** — Creates a Slack-signed request to `/slack/commands`
   with a task due 30 seconds in the future, then asserts the row exists in Postgres and
   prints the response body.

2. **Internal tick for reminder firing** — Calls `/internal/tick` with `CRON_SHARED_SECRET` to
   process due reminders and prints the response body (reminders_sent count).

3. **Google freeBusy query** — Fetches the stored refresh token from the database and calls
   the Google Calendar freeBusy API, printing the raw response body.

4. **/meet propose with capture endpoint** — Starts a local capture endpoint, sends
   `/meet propose` with `response_url` pointing to the capture endpoint, prints what would
   have been posted to `response_url`, and prints instructions for running `/meet` in real
   Slack (since a script cannot obtain a genuine Slack `response_url`).

5. **/meet book against proposal** — Uses the meeting ID from step 4 to call `/meet book`
   via a signed request and prints the created Google event ID.

### Requirements

- All environment variables must be set (DATABASE_URL, SLACK_SIGNING_SECRET, etc.)
- The app must be running locally with ngrok (PUBLIC_BASE_URL points to it)
- A real Slack workspace must be installed with the bot
- A Google account must be linked (redacted here — see the seeded workspace's `.env`)
- Test user IDs can be set via environment variables: TEST_TEAM_ID, TEST_USER_ID,
  TEST_CHANNEL_ID, TEST_UNLINKED_USER_ID

### Why this exists

This script exists precisely because mocks hid a real bug — the `userinfo.email` scope
was missing from `GOOGLE_SCOPES`, which caused `fetch_user_email()` to 401 in production
even though all tests passed. Driving every external call once against real services
catches bugs that unit tests with mocks cannot.
