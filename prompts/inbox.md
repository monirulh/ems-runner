<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt for reading labelled Research messages between tasks (Haiku). -->
Task {task_id}: read these Research/SkyUI messages (they apply to the approved plan):
{messages}
Messages parked in the backlog (do NOT act on them; only mention them):
{deferred}
For each applied message: add a short "Inbox" note to `docs/HANDOVER.md` saying what changes for which phase. If a correction changes work already marked done, or you cannot tell, write a `Q` to Research and finish `blocked`. Then write the newest message name (applied or parked) to `docs/collab/.last_read_dev`. Never edit or delete message files.
Finish `done`.
