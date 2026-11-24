<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for one SkyUI request (Sonnet; Opus for design-system questions). -->
Task {task_id}: handle the request `{request}` (path relative to the EMS project root; you work in apps/skyui).
1. Scope check (apps/skyui/CLAUDE.md): components, tokens, CSS variables, responsive layout, theme consistency are in scope; pages, forms, workflows, business features and backend logic are not. Out of scope → write a refusal message to `docs/collab/to-research/` and `docs/collab/to-dev/` (Kind: refusal) with reasons and technical context; finish `refused`.
2. Impact check before changing anything: which tokens, variables and components change, which existing screens use them. Run the visual regression check (CLAUDE.md). If anything outside the request could change → write the impact note to `docs/collab/to-research/` and finish `needs_approval` (waiting_for `SKYUI_<request file date_number>.md`).
3. Otherwise build on a `skyui/<request>` branch, test, run the regression check again, release a tag as SkyUI does today, and send the answer to `docs/collab/to-research/` and a copy to `docs/collab/to-dev/` (Kind: answer, About: the request id, Release tag).
Notes: {notes}
{attempt_note}
