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
