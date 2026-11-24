<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for the independent group review (Opus, fresh run that did not write the code). -->
Task {task_id}: independent review of {unit} (round {round}). You did not write this code.
Read ONLY: `{design_file}`, the cards of {phases}, the diffs of the `phase/*` branches of {unit} against `main`, and template 4 (review checklist) in `docs/collab/TEMPLATES.md`.
Write `{review_file}` (round {round} section): findings as Must fix / Should fix / Note, each with file and line.
Finish with `done` if there is no Must-fix item, else `changes_required` with `items` = the Must-fix list (one line each).
{attempt_note}
