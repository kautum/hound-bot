# Phase 0 Runbook — Gates & Setup

Five things, in this order. Each one needs your own accounts and credentials — none of it
can be done by an agent on your behalf, and that's deliberate: two of these gates can
reshape the whole architecture, so we find out now, not in week 8.

Do them in order. After each one, report back what happened (paste the actual output,
error message, or screen text — not a paraphrase) and we move to the next unit.

---

## Gate 0.1 — Devin credits actually spend, and the CLI drives cleanly

**Why:** The subscription is expired but credits should still exist. If the CLI doesn't
authenticate or drive the way the plan assumes, the entire Devin/Claude work split needs
rethinking before we design around it.

1. Go to `https://app.devin.ai` → Settings → Billing. Confirm the credit balance shown
   (~$480 expected) and screenshot or note the exact figure.
2. Install the Devin CLI (check `https://docs.devin.ai` for the current install command —
   it changes; don't trust a remembered one).
3. Authenticate it (`devin auth login` or whatever the current docs say).
4. Run one trivial task non-interactively, e.g.:
   ```
   devin -- "Create a file called hello.txt containing the word hello, in a new repo."
   ```
5. Report back: did it authenticate, what did the output look like, what was the exit
   code, and did the credit balance move afterward.

**Note down for me:** CLI install command that worked, auth method, sample output shape,
ACU/credit cost of that one trivial task.

---

## Gate 0.2 — Slack account, workspace, and app

**Why:** You've never had a Slack account. Everything downstream (OAuth, Block Kit,
slash commands) needs a real workspace to build against.

1. Create a Slack account at `https://slack.com/get-started` if you don't have one.
2. Create a **new workspace** dedicated to this project (don't use a work/personal one
   you already belong to) — call it something like "kautum-dev" or similar.
3. Go to `https://api.slack.com/apps` → **Create New App** → **From scratch**.
   Name it (e.g. "Workplace Assistant Dev"), pick the workspace you just made.
4. On the app's **Basic Information** page, note down (don't paste the actual secret
   values into chat — just confirm they exist and where they are):
   - **Signing Secret**
   - **Client ID** and **Client Secret** (under App Credentials)
5. Don't add scopes or install it yet — that's Unit 2.1, once we've written the OAuth
   handler. Just confirm the app exists and you can see its dashboard.

**Note down for me:** workspace name, app name, confirmation the three credentials exist
(not the values themselves — those go straight into a local `.env`, never into chat).

---

## Gate 0.3 — Google Cloud project + the verification probe

**Why:** `calendar.freebusy` and `calendar.events` are sensitive scopes. Until Google
verifies the app, refresh tokens expire after 7 days — the calendar feature would work
perfectly through the build, then silently die a week later. We need to know now whether
verification is realistic, or whether we're deliberately accepting the 7-day/100-user
Testing limits for a portfolio demo.

1. Go to `https://console.cloud.google.com` → create a new project (e.g.
   "slack-workplace-assistant").
2. **APIs & Services → Library** → enable **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - Add scopes: `.../auth/calendar.freebusy` and `.../auth/calendar.events`
   - Fill in the minimum required fields (app name, support email, developer contact)
4. **Try to publish**: look for the button to move from "Testing" to "In production".
   Click it (or as far as it lets you go) and read exactly what Google demands —
   privacy policy URL, verified domain, homepage, scope justification, demo video, etc.
5. **This is a `[YOUR CALL]` decision, not mine to make silently:** once you see
   Google's actual requirements, decide — pursue verification, or accept Testing limits
   (7-day tokens, 100-user cap, unverified-app warning) and say so plainly in the README.
   Either is a legitimate portfolio choice. Bring me what Google actually asked for and
   we decide together.

**Note down for me:** the exact list of requirements Google showed you, and which way
you're leaning.

---

## Gate 0.4 — Can two Gmail accounts see each other's free/busy?

**Why:** By default, two personal Gmail accounts do **not** share free/busy data. If this
doesn't work, the Phase 4 verification step ("two Google accounts with overlapping busy
blocks") would return empty results and look exactly like a bug in the scheduling code —
hours of debugging in the wrong file. We find out now, cheaply.

1. You'll need a second Gmail account — a spare one, or create a throwaway one. This also
   doubles as the second account needed later for Phase 2's two-workspace isolation test.
2. In Account A's Google Calendar, create an event that blocks a specific time window.
3. In Account B's Google Calendar, try **Other calendars → Add → Subscribe to calendar**,
   entering Account A's email. See what it shows — nothing, "busy" blocks only, or full
   event details.
4. Try the reverse direction too (B's calendar visible to A).
5. If it doesn't work by default, look at Account A's **Settings → Share with specific
   people** and explicitly grant Account B "See only free/busy" access, then retry step 3.

**Note down for me:** did it work with zero configuration, or did it need explicit
sharing — this decides whether the bot's onboarding needs to ask users to share
calendars, or whether `calendar.freebusy` with each person's own OAuth token is enough
on its own (which is what the plan currently assumes).

---

## Gate 0.5 — Hello-world bot

**Why:** Thirty free minutes seeing a bot say "hi" demystifies the whole project before
any real architecture gets built. This is the one gate we do together, live — come back
once 0.2 is done and we'll write the ~30 lines together using Socket Mode (no public URL
needed for this throwaway version).

**Depends on:** Gate 0.2 (needs the Slack app's Bot Token and Signing Secret).

---

## When you're back

Report each gate's outcome (in order, or whichever you got to) and we move to Phase 1.
If any gate produces a surprising result — Google refuses verification outright, the two
Gmail accounts flatly can't see each other even with sharing enabled, Devin's CLI doesn't
behave as documented — stop and flag it before doing the next one. That's exactly the
kind of result these gates exist to catch early.
