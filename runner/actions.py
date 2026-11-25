# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Shared runner actions (start, stop, pause, resume, set-duration, status, review) used by both the `ems` CLI and the Telegram menu.

import glob
import os
import re
import time

from .common import fmt_dur, fmt_dur_s, hm, iso, now, parse_duration, parse_until


def daemon_alive(eng):
    pidfile = eng.store.path("daemon.pid")
    hb = eng.store.path("heartbeat")
    try:
        pid = int(open(pidfile).read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    try:
        age = now() - float(open(hb).read().strip())
    except (OSError, ValueError):
        age = None
    return pid, age



def check_name(cmd):
    """Plain-word name of a verify command (the raw command is shown on a second line)."""
    m = re.search(r"run-tests\b.*?--app[ =]+(\S+)", cmd)
    if m:
        return "full test suite: %s" % m.group(1)
    m = re.search(r"run-tests\b.*?--module[ =]+(\S+)", cmd)
    if m:
        return "tests: %s" % m.group(1)
    c = cmd.strip()
    if c.startswith("node qa/") or c.startswith("python3 qa/"):
        return "QA screenshots run"
    if c == "ls" or c.startswith("ls ") or c.startswith("test -f ") or c.startswith("test -d "):
        return "file check"
    if re.search(r"\blist-apps\b", c):
        return "installed apps check"
    return "check command"


def verifying(cur):
    return bool(cur) and cur.get("step") == "verifying"


def now_block(eng, st=None, compact=False):
    """Lines that say what the runner is doing right now (agent or checks), in words."""
    st = st or eng.state()
    cur = st.get("current")
    if not cur:
        return []
    t = now()
    if verifying(cur):
        n, total = cur.get("verify_index") or 0, cur.get("verify_total") or 0
        limit = eng.cfg.getfloat("verify", "timeout_minutes", 40)
        ended = cur.get("agent_ended") or t
        if n:
            run = fmt_dur_s(t - (cur.get("verify_started") or t))
            check = "check %d of %d: %s" % (n, total, cur.get("verify_name") or "check command")
        else:
            run, check = "", "preparing the checks"
        if compact:
            out = ["%s · VERIFYING" % cur["task"],
                   "Agent finished (%s) at %s. The runner is now checking the work itself." % (
                       cur.get("agent_result") or "done", time.strftime("%H:%M", time.localtime(ended))),
                   check[0].upper() + check[1:]]
            if n:
                out.append("Running %s · limit %dm" % (run, limit))
            return out
        out = ["Now:         %s — VERIFYING (agent finished %s, said %s)" % (
            cur["task"], time.strftime("%H:%M:%S", time.localtime(ended)), cur.get("agent_result") or "done"),
            "             %s%s" % (check, (" — %s (limit %dm)" % (run, limit)) if n else "")]
        if n and cur.get("verify_cmd"):
            out.append("             cmd: %s" % cur["verify_cmd"][:160])
        return out
    run = fmt_dur_s(t - cur["started"])
    if compact:
        return ["%s · AGENT WORKING on %s — %s" % (cur["task"], cur["model"], run)]
    return ["Now:         %s — AGENT WORKING on %s — %s" % (cur["task"], cur["model"], run)]


def pause_reply(eng, cur):
    """Reply when pause is asked while a task runs (RUNNER-PAUSE-1)."""
    if verifying(cur):
        n, total = cur.get("verify_index") or 0, cur.get("verify_total") or 0
        what = "verifying (check %d of %d: %s)" % (n, total, cur.get("verify_name") or "check command") if n else "verifying"
        return ("Pause requested. %s is %s. It will finish its checks; then the runner pauses." % (cur["task"], what))
    return ("Pause requested. %s is running (agent working, %s). It will finish, including its checks; then the "
            "runner pauses." % (cur["task"], fmt_dur(now() - cur["started"])))


# every action returns (ok, text); callers print or send the text

def start(eng, duration=None, until=None):
    if not eng.state().get("baseline"):
        return False, "Run `ems init` first."
    try:
        seconds = parse_duration(duration) if duration else None
        end = parse_until(until) if until else None
    except ValueError as e:
        return False, str(e)
    st = eng.start_session(seconds=seconds, until=end)
    return True, "Session started: works until %s (%s)." % (hm(st["session"]["end"]), fmt_dur(st["session"]["end"] - now()))


def set_duration(eng, duration, from_now=False):
    """CLI: `3h` counts from the session start. Menu (from_now=True): `3h` counts from now. `07:30` is a clock time."""
    st = eng.state()
    if not st.get("session"):
        return False, "No session is running; use `ems start --for %s`." % duration
    try:
        if ":" in duration:
            end = parse_until(duration)
        else:
            end = (now() if from_now else st["session"]["start"]) + parse_duration(duration)
    except ValueError as e:
        return False, str(e)
    st["session"]["end"] = end
    eng.update_state(session=st["session"])
    eng.log("session end changed to %s" % iso(end))
    return True, "Session now ends at %s." % hm(end)


def pause(eng, now_=False):
    """Default: the running task finishes (agent and checks), then the runner pauses (RUNNER-PAUSE-1).
    now_=True is the emergency path: the running task is interrupted and will be rerun."""
    st = eng.state()
    cur = st.get("current")
    if now_ and cur:
        eng.update_state(interrupt="pause")
        return True, "Interrupting %s (emergency pause); it will be checkpointed, rerun later, and the runner paused." % cur["task"]
    if cur and st["mode"] in ("running", "waiting"):
        eng.update_state(pause_requested={"after": cur["task"], "at": now()})
        eng.log("pause requested; %s will finish first" % cur["task"], kind="pause_requested", task=cur["task"])
        return True, pause_reply(eng, cur)
    eng.update_state(mode="paused", pause_reason="owner", pause_requested=None)
    return True, "Paused. Nothing was running."


def resume(eng):
    st = eng.state()
    if not st.get("session") or now() >= st["session"]["end"]:
        return False, "The session has ended. Start a new one with `ems start` (default %sh)." % \
            eng.cfg.get("session", "default_hours", "5")
    eng.update_state(mode="running", pause_reason=None, wait=None, pause_requested=None)
    eng.log("resumed by owner")
    return True, "Resumed; session ends %s." % hm(st["session"]["end"])


def stop(eng):
    st = eng.state()
    if st.get("current"):
        eng.update_state(interrupt="stop")
        return True, "Stopping: this interrupts the running task. %s will be checkpointed, then the runner sleeps." % st["current"]["task"]
    eng.end_session("stopped by owner")
    return True, "Session stopped; the runner is asleep."


def status_text(eng):
    st = eng.state()
    data = eng.tasks()
    alive = daemon_alive(eng)
    pr = st.get("pause_requested")
    mode = "%s%s" % (st["mode"], (" (%s)" % st["pause_reason"]) if st.get("pause_reason") else "")
    if pr and st["mode"] != "paused":
        mode += " — pause requested (after %s)" % pr.get("after")
    out = ["Mode:        %s" % mode,
           "Daemon:      %s" % ("running, pid %d, heartbeat %ss ago" % (alive[0], int(alive[1] or 0)) if alive else "NOT running")]
    if st.get("session"):
        out.append("Session:     until %s (%s left)" % (hm(st["session"]["end"]), fmt_dur(st["session"]["end"] - now())))
    if st.get("wait"):
        w = st["wait"]
        out.append("Waiting:     %s until %s — %s" % (w["reason"], hm(w["until"]), w.get("detail", "")))
    blocks = {m: u for m, u in (st.get("model_blocks") or {}).items() if u > now()}
    if blocks:
        out.append("Model limit: %s" % ", ".join("%s until %s" % (m, hm(u)) for m, u in blocks.items()))
    out += now_block(eng, st)
    out.append("Unit:        %s (done: %s)" % (data.get("unit"), ", ".join(data.get("units_done", [])) or "none"))
    counts = {}
    for t in data["tasks"]:
        key = "verifying" if t["status"] == "running" and t.get("verify_progress") else t["status"]
        counts[key] = counts.get(key, 0) + 1
    out.append("Tasks:       %s" % (", ".join("%s %d" % kv for kv in sorted(counts.items())) or "none yet"))
    if verifying(st.get("current")):
        nxt = eng.peek_next(assume_done=st["current"]["task"])
        out.append("Next:        after the checks pass: %s" % (nxt or "nothing queued"))
    else:
        out.append("Next:        %s" % (st.get("next_action") or "-"))
    cp = st.get("last_checkpoint")
    if cp:
        out.append("Checkpoint:  %s — %s %s" % (iso(cp["at"]), cp["why"], ", ".join(cp.get("commits", []))))
    for t in [t for t in data["tasks"] if t["status"] == "blocked"]:
        b = t.get("blocked") or {}
        out.append("Blocked:     %s — %s %s %s" % (t["id"], b.get("reason"), b.get("waiting_for") or "", (b.get("note") or "")[:80]))
    return "\n".join(out)


def resolve_review(eng, target):
    """'G01', 'P003' or 'last' -> (phases, label) or (None, error text)."""
    target = target.strip()
    root = eng.cfg.project_root
    if target.lower() in ("last", "latest"):
        runs = [d for d in glob.glob(os.path.join(root, "qa", "out", "P[0-9][0-9][0-9]_*")) if os.path.isdir(d)]
        if not runs:
            return None, "no QA run yet"
        phase = os.path.basename(max(runs, key=os.path.getmtime))[:4]
        return [phase], phase
    if re.fullmatch(r"[Pp]\d{3}(\.\w+)?", target):
        phase = target[:4].upper()
        return [phase], phase
    from .plan import unit_by_id
    u = unit_by_id(eng.seq, target)
    if not u:
        return None, "unknown group or phase %s (use e.g. G01, P003 or last)" % target
    return u["phases"], target


def review(eng, target):
    """Builds the review page. Returns (ok, text, info) with info = {path, phases, label, problems}."""
    from .review import build, collect_problems
    phases, label = resolve_review(eng, target)
    if phases is None:
        return False, label, None
    root = eng.cfg.project_root
    path = build(root, phases, label)
    return True, "review page: %s" % path, {"path": path, "phases": phases, "label": label,
                                            "problems": collect_problems(root, phases)}
