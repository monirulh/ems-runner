<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for the Research check of a group design before automatic approval (Opus, AUTO-Q10). -->
Task {task_id}: act as RESEARCH, not Development. Check the design `{design_file}` for {unit} (phases {phases}) before it is approved automatically. You did not write it. Write nothing except the report.

Read: the design; the cards of {phases} (`docs/collab/phases/`, for P000 `docs/collab/prompts/P000.md`); the decisions they quote (registers in `docs/research/`, only the IDs quoted); `docs/CONFLICTS.md`; `docs/collab/TEMPLATES.md` template 3.

Check, item by item:
1. Every card item is in the design, and nothing outside the cards or the approved plan is added.
2. Nothing contradicts a recorded decision, hard rule in `CLAUDE.md` (money only via ERPNext documents, no Education code, no change to Frappe/ERPNext/HRMS/SkyUI, approvals, secrets) or an open conflict.
3. Open questions: each one is either (a) a choice inside the card's scope with a recommendation that changes no decision, or (b) a real owner decision (new scope, changed decision, money or legal policy, a new phase number, a printed design not yet planned).
4. Template 3 sections are present (data model, fields, roles, ERPNext touchpoints, translations, patches and rollback, apply plan, Playwright steps).

Write `docs/reviews/{unit}_design_check.md`: one line per check with Pass/Fail and the reason; list each open question with (a) or (b).
Finish with EMS_RESULT status `approve` only if every check passes and no open question is (b). Otherwise status `owner` with `items` = the points the owner must decide (one line each). When in doubt, choose `owner`.
{attempt_note}
