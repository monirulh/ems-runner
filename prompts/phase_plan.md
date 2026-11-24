<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt that turns one phase card plus the approved group design into runner tasks (Opus). -->
Task {task_id}: break phase {phase} into executable tasks.

Read: `{card}`, `{design_file}` (approved), `docs/collab/approvals/{gate_design}`, `docs/HANDOVER.md`. Nothing else unless the card points to it.

Write the JSON file `{plan_file}` (create folders if needed) with this shape:
{{"phase": "{phase}", "tasks": [
  {{"id": "t01", "kind": "build|test|apply|qa|docs", "title": "...", "details": "what to do, files, which card lines it implements",
    "verify": ["commands the runner runs itself to prove the task is done"], "model": null}}
]}}
Rules:
- 3–10 tasks in build order; each one small enough for one run of about an hour; no task repeats another.
- Cover every card item and nothing outside the card or the approved design.
- `apply` tasks run `infra/scripts/<name>_apply.sh --dry-run` first, then back up, then the real run, in the same task.
- `qa` task runs `qa/{phase}_run.mjs` and makes the `_OK` or `_ISSUES` zip.
- Translations (`.po`) and the handover are added by the runner; do not list them.
- `verify` commands must start with one of: infra/scripts/dc.sh, infra/scripts/shell.sh, node qa/, python3 qa/, python3 -m pytest, flutter test, flutter analyze, dart test, git -C, test -f, test -d, ls.
- `model`: leave null (runner table), or "opus" only for security or money logic the card marks as Opus.
- If the phase cannot be planned as carded (missing card section, conflict with the design), write a `Q` and finish `blocked`.

Notes: {notes}
{attempt_note}
