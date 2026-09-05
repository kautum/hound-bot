# LEARNING-LOG.md

Copy this to a project root the first time that project runs a Tier 2 checkpoint.
It decides how much friction the project-mode protocol applies: concepts listed as
known are built without checkpoints; anything else gets them.

---

## Already known — never run a Tier 2 checkpoint on these

Seeded 2026-08-31. BTech Computer Science, MSc Data Science, built a production LLM
automation platform professionally.

Python syntax and idioms · pandas dataframe manipulation · numpy · basic matplotlib/seaborn ·
Jupyter notebooks · scikit-learn fit/predict/transform · train-test splitting as a concept ·
basic classification and regression metrics · git add/commit/push/branch · reading JSON and
CSV · REST API calls · calling LLM APIs and prompt basics · virtualenvs and pip ·
basic SQL SELECT/JOIN

## Covered and explained back correctly

Once a concept lands here, build with it — don't re-check it.

| Date | Concept | How it came up | Explain-back verdict |
|---|---|---|---|
| | | | |

## Fumbled — re-check these next time they appear

| Date | Concept | What I got wrong | Retry when |
|---|---|---|---|
| | | | |

## Decisions I made, and why

The fork I was given, which branch I picked, and the reason. This is the part worth
re-reading months later — the reasoning is what fades, not the code.

| Date | Decision | Options I was given | What I chose and why |
|---|---|---|---|
| 2026-09-05 | Build the full feature set (tasks, reminders, and calendar scheduling), not a stripped-down MVP | A minimal slice vs. the full "friendly everyday helping bot" within the $400 cap | Chose the full build — first project like this, but wanted to be corrected along the way rather than scoped down to something safe |
| 2026-09-05 | Full multi-tenant, installable by any Slack workspace | A single personal/demo workspace vs. a real OAuth-installable multi-tenant product | Chose full multi-tenancy — the point is showing an employer I can build something actually usable, not a script that only runs in my own workspace |
| 2026-09-05 | Google Calendar only for scheduling | Google-only vs. also supporting Outlook/Microsoft 365 | Picked Google (the recommended option) — later learned Microsoft's free developer program now blocks personal accounts anyway, so the interface is kept provider-agnostic but only Google gets built |

## `/ship` usage

Tier 2 off means a concept went by without a checkpoint. Logged so the skipping stays
visible rather than becoming the default.

| Date | Why I shipped | What went unchecked |
|---|---|---|
| | | |
