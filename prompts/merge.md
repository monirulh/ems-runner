<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt that merges a released group (only after the release approval file exists). -->
Task {task_id}: release {unit}. The owner's release approval is in `docs/collab/approvals/{gate_release}` (read it; follow any conditions).
Order (D47; review G00 R6-S2): the release gate refuses any commit made directly on `main`, so nothing is committed on `main`.
1. First, on the `phase/*` branch of each repo of {phases}: commit every open change. In the `docs` repo this last commit holds `docs/HANDOVER.md` with the release line and the SkyUI tag used, and a message to Research in `docs/collab/to-research/` (About: {unit}, Needs action: no) saying which branches are merged and tagged. Run the secret scan, then push the phase branch if a remote is configured.
2. Then, for each repo: if `main` does not exist, `git checkout -b main <phase branch>`; otherwise `git checkout main`. Then `git merge --no-ff <phase branch>` and `git tag {unit}`. If a remote is configured: `git push origin main`, then `git push origin {unit}`.
3. After the merge, commit nothing on `main`. The next phase's first commit goes to its own `phase/` branch. Put the merge result (merge commits, tag, pushes) in the task summary.
If a merge conflicts, do not force anything: finish `blocked` (reason `owner-action`).
{attempt_note}
