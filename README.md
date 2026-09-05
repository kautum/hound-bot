# Slack Workplace Assistant

A Slack bot any organisation can install: task/deadline tracking with automated chasing,
and meeting scheduling that reads participants' Google Calendars to find the earliest
mutual free slot.

## Status

**Phase 0 — Gates & setup.** No application code yet, on purpose. Three cheap experiments
happen first because each one can reshape the architecture: Google's app-verification
requirements, whether personal Gmail accounts share free/busy data at all, and whether
Devin CLI drives cleanly. See `RUNBOOK.md` for exactly what to do and in what order.

## Definition of done

> A stranger can install the bot into their own Slack workspace, link a calendar, create
> a task, and book a meeting — and the README shows it happening.

## Full spec

The complete architecture, security model, work split, and build plan live at:
`~/.claude/plans/alright-now-lets-glistening-yeti.md`

That file is the source of truth. This repo is where it gets built, one reviewed unit
at a time (see Part 14 of the plan for the working agreement).
