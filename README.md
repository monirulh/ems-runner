Copyright (c) 2026 Monirul Hasan. All rights reserved.
<!-- Operator guide for the EMS runner: setup, daily use, behaviour and limits. -->

# EMS runner

Runs the approved EMS plan unattended, one task per fresh agent run, between the owner's approval gates.
Decisions: AUTO-Q1–AUTO-Q9 (owner, 2026-11-25; Research register part 4). Python 3.9+ standard library only.

## 1. One-time setup on the Mac (about 15 minutes)

1. Put the runner command on your PATH (zsh):
   `echo 'alias ems="$HOME/development/erp_dashboard_dev/tools/ems-runner/ems"' >> ~/.zshrc && source ~/.zshrc`
2. Make the runner a git repo: `cd ~/development/erp_dashboard_dev/tools/ems-runner && git init -b main && git add -A && git -c user.name="Monirul Hasan" -c user.email=89monirulhasan@gmail.com commit -m "EMS runner v1"`
3. Local settings outside the repo, `~/.skysoft/ems-runner.ini`:
   ```
   [agent]
   cli_path = <output of: which claude>
   ```
4. Notifications, `~/.skysoft/secrets.env` (then `chmod 600 ~/.skysoft/secrets.env`):
   ```
   TELEGRAM_BOT_TOKEN=<from @BotFather /newbot>
   TELEGRAM_CHAT_ID=<from: ems telegram-chat-id, after you send your bot any message>
   NTFY_TOPIC=<a long random name: python3 -c "import secrets;print('ems-'+secrets.token_urlsafe(24))">
   ```
   Subscribe to the same topic in the ntfy phone app. The topic name works like a password.
5. `ems selftest` — 22 sandbox tests with a fake agent (no usage).
6. `ems doctor --live` — checks tools, disk, Docker, notifications and one tiny real Haiku run.
7. `ems notify-test` — you should get it on the Mac, Telegram and ntfy.
8. `ems init` — records the plan baseline and treats existing messages (D1–D30) as already handled.
9. `ems install` — background service (launchd): restarts the runner after crashes and reboots.

## 2. Daily use

| Command | What it does |
|---|---|
| `ems start` | work for the default 5 hours (`[session] default_hours`) |
| `ems start --for 2h` / `--until 06:00` | work for a set time |
| `ems set-duration 3h` / `07:30` | change the running session's end |
| `ems status` | mode, time left, current task and model, next action, blocked tasks, last checkpoint |
| `ems tasks [--all]` | the task list |
| `ems log [-f]` | the runner log; each run's full stream is in `var/logs/runs/` |
| `ems usage [--days 7]` | runs, estimated cost and tokens per model |
| `ems pause` / `ems pause --now` | pause after the running task finishes, agent and checks (wait for the "Paused" notice) / EMERGENCY: interrupt the running task, checkpoint, it is rerun |
| `ems resume` | continue a paused session (also after `/login`) |
| `ems stop` | end the session now (checkpoint, then sleep) |
| `ems unblock <task> [--note ..] [--model opus]` | release a task you have dealt with |
| `ems retry <task>` | retry a failed task |
| `ems progress` | progress table (also in `var/PROGRESS.md`) |
| `ems doctor --mobile` | also checks Flutter, adb, emulator, xcrun, CocoaPods |

## 3. How it works

- **Order** comes from `plan/sequence.json` (41 units, P000 + 154 phases, from master v11). A unit starts only after the previous unit's release approval (AUTO-Q1).
- **Per unit:** cards present → group design (Opus) → wait for `approvals/G<NN>_design.md` → per phase: plan (Opus writes `var/plans/P<NNN>.json`) → build/test/apply/qa tasks (Sonnet) → translations and handover (Haiku) → independent review (Opus, fresh run; up to 2 fix rounds) → wait for `approvals/G<NN>_release.md` → merge and tag → next unit.
- **Approval files** count only if they name the owner (OP-Q1 template); Research writes them. The guard blocks agents from writing them.
- **Done means verified:** the runner checks the expected output files and runs the task's verify commands itself (only commands on `[verify] allowed_prefixes`).
- **Failures:** two failures (or the same error twice) move the task to Opus; if Opus fails too, the task is blocked and you are notified. Nothing loops.
- **Checks stay visible:** while the runner verifies a task it says so (`ems status`: `VERIFYING`, check i of n, plain-word name, elapsed time and limit), keeps its heartbeat, answers Telegram, and obeys Stop. A check that runs longer than `[verify] notify_after_minutes` (default 3, 0 = off) sends one Telegram/macOS message per task.
- **Limits:** "You've hit your … limit · resets …" → checkpoint, sleep until the reset + 5 minutes, resume if the session still has time. An Opus- or Sonnet-only limit blocks only that model. 429/529/5xx: the agent retries; then the runner waits 5/15/30 minutes, then blocks. No network: it waits and probes. Login expired: it pauses and tells you.
- **Session end:** no new task starts; the current one gets `grace_minutes` (15), then it is interrupted and checkpointed (WIP commit on the `phase/*` branch only). The runner sleeps until `ems start`.
- **Restarts:** an interrupted task goes back to pending with a note to inspect partial work; it is never assumed done.
- **Messages from Research** (`docs/collab/to-dev/`) must carry `Kind:` — `correction`, `answer`, `card` (applied at the next task boundary), `new-requirement` (copied to `docs/backlog/`, never built), `exception` (enters at a group boundary once `approvals/EXC_<date>_<nnn>.md` exists). No `Kind:` → backlog (AUTO-Q7).
- **SkyUI:** a Development task that needs SkyUI writes an `R` message and blocks; the runner starts a SkyUI run in `apps/skyui` (rules in `apps/skyui/CLAUDE.md`). SkyUI refuses out-of-scope work, parks breaking changes for `approvals/SKYUI_<date>_<nnn>.md`, and answers with a tag; the waiting task then resumes.
- **Guard:** `guard/pretool_guard.py` runs before every tool call (destructive commands, approval files, research docs, secrets, Frappe/ERPNext/HRMS files, SkyUI boundaries, push/merge only in the release task).

## 4. Settings

Everything is in `config.ini` (models table, turns, session length, grace, escalation, limits, verify prefixes, notifications). Override any value in `~/.skysoft/ems-runner.ini`. Runtime files are in `var/` (gitignored).

## 5. Known limits

- The approval gates, Research's per-group cards (PF-Q1) and owner actions are real stops; the runner notifies you at each.
- Remaining subscription allowance is not readable by scripts; `ems usage` shows estimates per run.
- A MacBook with the lid closed can still sleep; on wake the runner reconciles and continues if the session has time.
- Intel Mac: Android work is fine; for iOS store releases plan an Apple Silicon or cloud Mac (Inference).

## Telegram consent gate (TG-CONSENT)

Off by default. Set `telegram = yes` under `[consent]` in `config.ini` to turn it on; `no` restores the old behaviour.
When a task stops at an owner gate, the runner sends a header (options, inline buttons) and the brief in parts of under 4,096 characters to every id in `recipients`. Replies are accepted only from those ids: tap a button, reply with the option number to the header message, or send `/approve G01-design 1`. A valid reply writes one new `Kind: answer` message in `docs/collab/to-research/`; Research then writes the approval file and the runner resumes as it does today. Needs `TELEGRAM_BOT_TOKEN` in `~/.skysoft/secrets.env`. State is in `var/consent.json`.

## Telegram control menu (TG-CONTROL)

Set `telegram = yes` under `[control]` in `config.ini` (`no` = menu ignored; the consent gate is unaffected). Send `ems` or `/ems` to the bot in a private chat: one menu message appears and every tap edits it in place. Items: On/Off (start with a duration, stop), Pause/Resume (after this task; "Pause now" is an emergency stop that interrupts the running task), Status, Duration, Review & screenshots. Only actions valid for the current state are shown; anything that changes the runner needs a Confirm screen (✅ Run / ⬅ Back); every level has Back. Typed shortcuts open the Confirm screen: `/ems pause [now]`, `/ems resume`, `/ems stop`, `/ems start [2h]`, `/ems duration 2h`; `/ems status` shows the status.

- Same listener as the consent gate (callback prefixes `c:` consent, `m:` menu); it runs in the daemon, also while a task is running. State: `var/consent.json` (update offset), `var/telegram_control.json` (open menus, nonces).
- Buttons are single-use and expire 10 minutes after the menu was last drawn; before ✅ Run the state is re-read and, if it changed, the menu is redrawn instead.
- Duration presets and the default are in `[control] presets` / `default_preset`. "Custom…" takes one reply: `2h30m`, `90m` or `06:30` (1 minute to 24 hours).
- Both ids in `[consent] recipients` may use everything; other senders and group chats are ignored. When one id changes the runner, the other gets a one-line notice. At most 5 changing actions per minute. Every action is logged to `var/telegram_control.log` (time, user id, action, result).
- Review sends the problems as text, the page as a file and key screens (Bangla light) as an album. Screenshots (group → phase → set) come from the latest `qa/out/P0NN_<ts>/screenshots/`: albums of 10, at most 30 pictures per request (then a note to use `ems open screens`), files over 10 MB skipped. No live screen capture.
- The `ems` commands and the menu call the same functions in `runner/actions.py`.
- Rollback: `[control] telegram = no`, restart the daemon.
