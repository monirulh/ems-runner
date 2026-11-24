Copyright (c) 2026 Monirul Hasan. All rights reserved.
<!-- The owner's everyday workflow with the EMS runner: routine, notifications, checking work, approvals, problems. -->

# EMS runner — your workflow

Version 1 · 2026-11-25 · Author: Monirul Hasan · Decisions: AUTO-Q1–AUTO-Q10 (register part 4 v12).

## 1. The idea in one paragraph

The runner on your Mac builds the approved plan by itself, group by group, and picks the model for each step. Designs are approved automatically when Research's check finds nothing for you to decide; releases are approved automatically when the review, the tests and the screenshots are clean. It stops for you only when something needs your decision, and it tells you by Telegram, ntfy and a Mac notice. You talk to Research in this Claude Project (desktop or phone); you control the runner with the `ems` command in Terminal.

## 2. Your daily routine (about 10 minutes)

**Morning**
1. Read the **08:00 daily summary** on Telegram (tasks done, auto-approvals, what waits for you).
2. If something waits for you, open a new chat in the Research Project and send `next`.
3. For a release on your always-check list (money, pilot go-live, prints): send `summarise G0N for release`, look at the screenshots, then `approve G0N release` or say what is wrong.

**Evening, before you sleep**
1. Plug the MacBook in and leave the lid open (Claude desktop app open).
2. In Terminal: `ems start --until 08:00`
3. Check `ems status` once: Mode `running`, Daemon `running`.
4. Sleep. The runner works, waits at limits, and stops only for real decisions.

During the day you can also run sessions: `ems start --for 3h`.

## 3. Notifications and what to do

| Notice | What it means | What you do |
|---|---|---|
| Starting G0N / P0NN finished | Progress | Nothing |
| Auto-approved: G0N design / release | The check was clean | Nothing (read later if you want; `ems open approvals`) |
| **Design needs you: G0N** | The check found points only you can decide | Research chat: `next` — it explains the points with options |
| **Release needs you: G0N** | Not clean, or a money/pilot/print group | Research chat: `summarise G0N for release` |
| **Cards needed from Research** | G08 onwards needs its cards | Research chat: `next` (Research writes them; you approve any new decision) |
| **Waiting for Research / Message for Research** | Development asked a question | Research chat: `next` |
| **Owner action needed** | Something only you can do (e.g. GitHub push, accounts, a print plan) | Read the text; do it or tell Research |
| **Toolchain missing** | Mobile tools not installed (before G06) | Install from the checklist, then `ems retry <task>` |
| Usage limit reached | Your Max plan limit | Nothing — it sleeps and resumes after the reset |
| **Task failed: …** | Failed even on Opus | Research chat: `look at failed task P0NN.t03` |
| **Login needed** | Claude login expired | Terminal: `claude`, then `/login`, then `ems resume` |
| Session ended | Your time is up | Nothing, or `ems start` again |

Bold = it is waiting for you. The Mac plays no sound (turn it on with `macos_sound = yes` under `[notify]` in `~/.skysoft/ems-runner.ini`).

## 4. When your Max plan limit is reached

Nothing to do. The runner saves its work, sleeps until the reset time shown in the notice, and continues if your session still has time. If only Opus is limited, Sonnet and Haiku work continues. If the session ends while it waits, it sleeps until your next `ems start`. `ems usage --days 7` shows which steps used the most.

## 5. Seeing the work

| What | How |
|---|---|
| Is it working, what now, how long left | `ems status` |
| Live view of what it reads, edits and runs | `ems watch` (Ctrl+C to leave; the runner keeps going) |
| All tasks and their state | `ems progress` or `ems open progress` |
| Screenshots of the built screens | `ems open screens` → folder `P0NN_<time>` → `screenshots/` and `REPORT.md` |
| Group designs | `ems open designs` |
| Reviews and design checks | `ems open reviews` |
| Approvals (manual and automatic) | `ems open approvals` (`AUTO_LOG.md` lists the automatic ones) |
| The plain log | `ems log -f` |

Screenshot names: `P012_S003_<screen>_<action>_<bn|en|ar>_<light|dark>.png`. Look first at the Bangla light pictures of every step.

## 6. Research chat prompts (copy, change the group name)

```
next
```
```
summarise G01 for release
```
```
approve G01 release
```
```
approve G01 design
```
```
G01 design: change <what>
```
```
revoke G01 design
```
```
look at failed task P003.t02
```
```
new requirement: <describe it>
```

New requirements go to the backlog and are built only after the whole plan is done, unless you say `urgent exception: <what>` (it then enters at the next group boundary).

## 7. Terminal commands

| Command | Use |
|---|---|
| `ems start` / `--for 3h` / `--until 08:00` | Start working (default 5 h) |
| `ems set-duration 2h` / `07:30` | Change the running session |
| `ems pause` / `ems pause --now` / `ems resume` | Pause after the task / at once / continue |
| `ems stop` | End the session now (checkpoint, sleep) |
| `ems status`, `ems watch`, `ems progress`, `ems log -f` | See what is happening |
| `ems open screens|designs|reviews|approvals|progress` | Open in Finder |
| `ems usage --days 7` | Usage by model |
| `ems retry <task>` / `ems unblock <task> --note "…"` | After you fixed what blocked it |
| `ems doctor` (`--live`, `--mobile`) | Health check |

## 8. If something looks wrong

1. `ems stop` — the current task is checkpointed, nothing is lost.
2. `ems log -n 60` — copy the last lines.
3. Research chat: `runner problem:` and paste them.

The Mac keeps the runner alive after crashes and restarts. After a reboot it continues by itself if your session still has time.
