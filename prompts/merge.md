<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt that merges a released group (only after the release approval file exists). -->
Task {task_id}: release {unit}. The owner's release approval is in `docs/collab/approvals/{gate_release}` (read it; follow any conditions).
For each repo with `phase/*` branches of {phases}: merge them into `main` (no fast-forward), tag `{unit}`, push `main` and the tag if a remote is configured. Record the SkyUI tag used. Update `docs/HANDOVER.md` with the release line and send Research a message in `docs/collab/to-research/` (About: {unit}, Needs action: no) with the result.
If a merge conflicts, do not force anything: finish `blocked` (reason `owner-action`).
{attempt_note}
