<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for an unattended Research answer to a Development or SkyUI message (Opus, AUTO-Q11). -->
Task {task_id}: answer the message `{request}` as Research.

1. Read the message and every file, log line and decision it names. Check the newest `docs/collab/to-dev/` and `docs/collab/to-research/` messages so you do not repeat or contradict an answer already given (two messages may ask the same thing; answer both in one reply).
2. Verify claims yourself where you can (open the files, count, read the log). Do not trust the question's summary alone.
3. Sort each item: (A) settled by existing decisions/rules, (B) small in-scope choice you may take (see system rules), (C) owner decision.
   A message `From: Runner` titled "owner action" means a task stopped for the owner. Check each reason the same way. Only something the owner alone can do or decide is C: a command the guard refuses (destructive cleanup), an account, a credential, money, a store, a pilot go-live. For a C action, put the exact command or steps in the owner file.
4. If ALL items are A or B: write one new message `docs/collab/to-dev/<YYYY-MM-DD>_<next number>_D<next D>-<short-slug>.md` (for SkyUI questions: `docs/collab/to-skyui/<date>_<next number>_A-<slug>.md`, and a copy of the answer to `to-dev/` if a Development task waits). Use the message template in `docs/collab/TEMPLATES.md`, the copyright header, `Kind: answer`, `About:` with the same ids as the question, and name the question file in the first line of the body. Quote the decision IDs you apply. Label each choice you made yourself "Decision (Research, automatic under AUTO-Q11; the owner can revoke)".
5. Record: append an entry to the newest decisions register part in `docs/research/` (the one `docs/research/INDEX.md` lists as open for new entries), bump its version line and its INDEX.md row. Add the changed file names to `docs/research/SYNC_TO_PROJECT.md` (one line each, with the date) so the owner's chat Research copies them to the claude.ai Project. Write the question's file name to `docs/collab/.last_read_research` if it is newer than the one there.
6. If ANY item is C: still answer every A/B item that does not depend on it (step 4 and 5, Kind: answer, and say which item waits for the owner). Then write `docs/collab/owner/<YYYY-MM-DD>_OQ_{task_id}.md` with: the question file, a two-line context, and for each owner item a numbered question with 2–4 options, the recommended option first marked "(Recommended)", and one line on what each option changes. Finish `blocked`, reason `owner`, `waiting_for` = that file name. Do not answer the C items yourself.
7. Words: plain English, short sentences. The banned word (the AI tool's name, see `BANNED` in the hooks) must not appear in any line Development will copy into code or commit messages.

Notes from earlier attempts:
{notes}
{attempt_note}
