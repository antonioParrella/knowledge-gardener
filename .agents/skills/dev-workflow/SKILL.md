---
name: dev-workflow
description: Develop a change in Knowledge Garden safely, from a short-lived branch through tests, merge and watchdog restart. Use when changing code or prompts in this repository, preparing a feature or fix to merge, or resuming after a merge. Keeps the deployed working tree and the running watchdog in sync.
---

# Development workflow

`main` is the deployed branch. The watchdog imports Python files from the
working tree at start-up, not from a committed or pushed revision. Work on a
short-lived branch and merge only when the suite passes; a merge takes effect
only after restarting the watchdog.

1. Branch from `main` with `git switch -c fix/<slug>` or `git switch -c feat/<slug>`.
2. Make the change, keep commits focused, and add or adjust tests. A `str → str`
   helper or routing/gate decision needs a Tier 1 or 2 test.
3. Run `pytest`. After touching a prompt or model route, also run the relevant
   Tier 3 test described in `../run-tests/SKILL.md`.
4. Merge and push with `git switch main && git merge <branch> && git push`.
5. Restart the watchdog from `main`. Never leave it running against a
   half-merged working tree.

This is a solo-project workflow, not git-flow: its purpose is to keep `main`
runnable and make the running process match it.
