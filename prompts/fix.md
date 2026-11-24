<!-- Copyright (c) 2026 Monirul Hasan. All rights reserved. -->
<!-- Prompt that fixes the review's must-fix items (Sonnet). -->
Task {task_id}: fix the Must-fix items from `{review_file}` for {unit}:
{items}
Change only what the items require, on the existing phase branches; run the affected tests; commit. Do not change the design or scope; if an item needs a design change, write a `Q` and finish `blocked`.
{notes}
{attempt_note}
