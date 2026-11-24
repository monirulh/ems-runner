<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for the end-of-phase handover (Haiku). -->
Task {task_id}: write `docs/HANDOVER.md` for {phase} using template 5 in `docs/collab/TEMPLATES.md`.
Gather facts only from: `git log` on the phase branches, `qa/out/{phase}_*` reports, and the runner progress file `tools/ems-runner/var/PROGRESS.md`. Do not invent results. Also write `docs/collab/prompts/` next-prompt file only if CLAUDE.md requires it for manual sessions.
Finish `done`.
