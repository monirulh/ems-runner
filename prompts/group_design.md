<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for a group design task (Opus). -->
Task {task_id}: write the group design for {unit} (phases {phases}).

1. Read `CLAUDE.md`, `docs/HANDOVER.md`, `docs/STATUS.md`, the cards of {phases} in `docs/collab/phases/` and the approved designs of earlier groups only where a card depends on them.
   Extra source for this unit (if set): {design_prompt}. If it tells you to read inbox messages (for G00: D1–D30, plus D31), read exactly those; they predate the runner.
2. Check `docs/research/INDEX.md` versions against each card. If a version differs, write a `Q` to Research and finish `blocked` (reason `question`).
3. Write `{design_file}` following template 3 in `docs/collab/TEMPLATES.md` (Mermaid data model and flows, fields, role matrix, ERPNext touchpoints, translations, patches and rollback, apply-script plan, Playwright steps, open questions). Every open question that blocks building becomes a `Q` message.
4. Do not build anything. The owner approves the design in the Research chat; Research then writes `docs/collab/approvals/{gate_design}`.

Notes from earlier attempts and the runner:
{notes}
{attempt_note}

Finish with EMS_RESULT `done` when `{design_file}` is complete (or `blocked` with the Q file name).
