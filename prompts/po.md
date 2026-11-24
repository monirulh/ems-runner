<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for the translation check of a phase (Haiku). -->
Task {task_id}: translation check for {phase}.
Find UI strings added on the `phase/{phase}-*` branches (git diff against `main`). Each must be in `bn.po` and `ar.po`. Add missing entries with the comment `#. ai-draft`, using `apps/skyui/docs/glossary_bn.csv`; leave `msgstr` empty when unsure. Stored data is never translated. Commit on the phase branch.
Finish `done` with the count of entries added, or `failed` if the strings cannot be extracted.
{attempt_note}
