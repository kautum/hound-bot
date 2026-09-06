# Privacy Policy

**Status: template.** Fill in the bracketed fields before hosting this on the
Student Pack domain and submitting Google's app verification (Part 0.5 /
Unit 7.4b) — Google requires a live URL for this document, not a draft.

**Last updated:** [DATE]
**Contact:** [YOUR EMAIL]

## What this bot is

[APP NAME] is a Slack app that tracks tasks, sends reminders, and schedules
meetings by reading participants' Google Calendar availability. This policy
covers what data it collects and why.

## Data collected

| Data | Source | Why |
|---|---|---|
| Workspace ID, encrypted bot token | Slack OAuth install | Lets the bot post and respond in your workspace |
| Slack user ID, timezone | Slack events and commands | Scheduling and reminders in your local time |
| Task titles, due dates, assignees | Things you tell the bot | The core feature |
| Free/busy time blocks (never event titles, attendees, or locations) | Google Calendar `calendar.freebusy`, only for people who explicitly link their calendar | Finding a mutual meeting time |
| Google account email | Google OAuth linking | Inviting you to a booked meeting as a calendar attendee |

The bot never reads calendar event details — only whether a time is busy or
free. It never reads Slack channel history beyond messages it's directly
mentioned in or commands sent to it.

## Data retention and deletion

Data is retained while your workspace has the app installed. Uninstalling
the Slack app or unlinking your Google Calendar marks the corresponding
record inactive; on request to [YOUR EMAIL], all data tied to your workspace
or account is deleted within 30 days.

## Third parties

Task and reminder text may be sent to Groq's API for natural-language
processing. Groq does not train on this data at any tier (see [Groq's data
policy](https://console.groq.com/docs/your-data)). Free/busy data is
exchanged only with Google's Calendar API, under the scopes you explicitly
grant.

## Security

Workspace bot tokens and Google refresh tokens are encrypted at rest.
Requests are verified against Slack's signing secret before being processed.
See this repository's `ARCHITECTURE.md` for the full security model.

## Changes to this policy

[HOW YOU'LL NOTIFY USERS OF CHANGES, e.g. "posted here with an updated date"]
