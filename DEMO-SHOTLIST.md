# Demo video shot list

Target: 60-90 seconds, screen-recorded against the running local app + ngrok tunnel (or a
hosted URL, if that ever happens — the shots are identical either way). Narrate briefly
over each shot or caption it; the point is showing real Slack, not explaining code.

**Before recording:** `/invite @Hound` (or whatever the app's display name is) into
whichever channel you'll demo `/task` and `@mention` in — see `LIVE-FIRE.md`'s note on
`not_in_channel`. This is a one-time setup step, not part of the recording.

## Shots, in order

1. **`/task add @you Ship the demo video | <a due time ~30-60s from now>`**
   Shows the confirmation message land immediately. Proves the slash command works and
   parses a real due date.

2. **Wait for the reminder DM to arrive** (fires on the next 5-minute tick in production;
   locally, `/internal/tick` fires every 2 seconds via the worker's poll loop, so this is
   near-instant). Show the DM appearing in Slack. This is the moment that actually proves
   the job queue + scheduler pipeline works, not just that a command was accepted.

3. **`/task list`** — shows the Block Kit "Mark done" button rendering, not plain text.
   Click it. The task disappears from a follow-up `/task list` and a confirmation appears
   — this proves the new `/slack/interactions` endpoint end to end, not just the button
   rendering.

4. **`/task add @teammate Weekly sync notes | <due date> | repeat:7`** then `/task done
   <id>` (or click its button) — shows a second occurrence get created automatically,
   due exactly 7 days later. Proves recurrence without waiting a week for it to matter.

5. **`@Hound digest`** in a channel Hound's been invited to (or DM Hound directly).
   Shows the LLM-routed digest reply — real Groq call, real tool execution, real numbers
   from Postgres. If it doesn't come back in the digest's exact format, that's expected
   model non-determinism (see `LIVE-FIRE.md`) — just re-ask; a second take is fine.

6. **`/meet @you @teammate 30 | <window start> | <window end>`** — proposes a slot and shows
   a "Book" button, not a UUID to copy-paste. **This step needs the Google Calendar link to
   be live** (published-to-production + re-linked — see `PROJECT-WIKI.md`'s current
   blockers). If that's not done by the time you record, either skip this shot and caption
   "calendar linking demoed separately" or wait until it's unblocked. Don't fake it.

7. **(If step 6 ran)** Click "Book" — shows the real Google Calendar event being created and
   a "Cancel" button appearing in its place. Click "Cancel" — shows it actually being
   deleted from the real calendar, not just marked cancelled in our own DB.

8. **Open Hound's App Home tab** in Slack (the "Home" tab next to Messages) — shows the same
   open-tasks list with "Mark done" buttons, refreshed live. Proves it's a persistent app
   surface, not just slash commands.

9. **(Optional)** `/task reassign <id> @someone-else` — shows the task moving to a new
   assignee; a follow-up reminder would now DM the new person instead, and that DM also
   carries a "Mark done" button.

10. **(Optional, one-time)** Reinstall the app (or show a screenshot) to demonstrate the
    HTML install-success page and the welcome DM — the first thing an admin actually sees,
    not a JSON blob.

## What NOT to show

- Terminal/code — this is a product demo, not a code walkthrough. `README.md` and
  `PROJECT-WIKI.md` already carry the engineering story for anyone who wants it.
- Real personal identifiers on screen (email, team ID) beyond what Slack's own UI already
  shows to the person recording — nothing here is being distributed publicly, but no
  reason to zoom in on them either.

## After recording

Save as `demo.mp4` (or `.gif` if short enough) at the repo root, embed it in `README.md`'s
top section, and update `README.md`'s test-count line if further work changed the count
since 193.
