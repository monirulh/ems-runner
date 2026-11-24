<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for one build, test, apply, qa or docs task of a phase (usually Sonnet). -->
Task {task_id} ({phase}, unit {unit}): {title}

What to do:
{details}

Sources: `{card}`, `{design_file}` (approved). Work on branch `phase/{phase}-<name>` in the repos you change (create it from `main` if missing).
Verify commands the runner will run after you finish:
{verify}

Before finishing: run the relevant tests yourself; commit your work on the phase branch (author Monirul Hasan, no AI co-author lines, message starts with "{phase}:"). New UI strings go into `bn.po` and `ar.po` in the same commit.
If SkyUI must add or change a shared component, write an `R` message to `docs/collab/to-skyui/` and finish `blocked` (reason `skyui`, waiting_for = that file name).

Notes from earlier attempts and the runner:
{notes}
{attempt_note}
