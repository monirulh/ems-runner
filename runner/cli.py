# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# The `ems` command: start, pause, resume, stop, status, tasks, log, usage, doctor and setup helpers.

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time

from .common import RUNNER_HOME, Config, expand, fmt_dur, hm, iso, load_secrets, now, parse_duration, parse_until
from .engine import MODEL_KINDS, Engine
from . import notify

LABEL = "com.skysoft.ems-runner"
PLIST = expand("~/Library/LaunchAgents/%s.plist" % LABEL)


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


def ensure_daemon(eng):
    if daemon_alive(eng):
        return
    if os.path.exists(PLIST) and shutil.which("launchctl"):
        subprocess.run(["launchctl", "kickstart", "gui/%d/%s" % (os.getuid(), LABEL)], check=False)
        time.sleep(2)
        if daemon_alive(eng):
            return
    log = open(os.path.join(eng.cfg.var_dir, "logs", "daemon.out"), "a")
    subprocess.Popen([sys.executable, "-m", "runner.engine"], cwd=RUNNER_HOME, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    print("runner daemon started in the background (install the background service with `ems install`).")


def cmd_init(eng, a):
    st = eng.init()
    print("Initialised. Plan baseline sha %s…; %d existing messages treated as already handled."
          % ((st["baseline"] or {}).get("master_sha", "?")[:12], sum(len(v) for v in st["seen"].values())))


def cmd_start(eng, a):
    if not eng.state().get("baseline"):
        print("Run `ems init` first.")
        return 1
    seconds = parse_duration(a.duration) if a.duration else None
    until = parse_until(a.until) if a.until else None
    st = eng.start_session(seconds=seconds, until=until)
    print("Session started: works until %s (%s)." % (hm(st["session"]["end"]), fmt_dur(st["session"]["end"] - now())))
    ensure_daemon(eng)


def cmd_set_duration(eng, a):
    st = eng.state()
    if not st.get("session"):
        print("No session is running; use `ems start --for %s`." % a.duration)
        return 1
    end = parse_until(a.duration) if ":" in a.duration else st["session"]["start"] + parse_duration(a.duration)
    st["session"]["end"] = end
    eng.update_state(session=st["session"])
    eng.log("session end changed to %s" % iso(end))
    print("Session now ends at %s." % hm(end))


def cmd_pause(eng, a):
    st = eng.state()
    if a.now and st.get("current"):
        eng.update_state(interrupt="pause")
        print("Interrupting %s; it will be checkpointed and the runner paused." % st["current"]["task"])
    else:
        eng.update_state(mode="paused", pause_reason="owner")
        print("Paused%s." % (" after the current task" if st.get("current") else ""))


def cmd_resume(eng, a):
    st = eng.state()
    if not st.get("session") or now() >= st["session"]["end"]:
        print("The session has ended. Start a new one with `ems start` (default %sh)." %
              eng.cfg.get("session", "default_hours", "5"))
        return 1
    eng.update_state(mode="running", pause_reason=None, wait=None)
    eng.log("resumed by owner")
    print("Resumed; session ends %s." % hm(st["session"]["end"]))
    ensure_daemon(eng)


def cmd_stop(eng, a):
    st = eng.state()
    if st.get("current"):
        eng.update_state(interrupt="stop")
        print("Stopping: %s will be interrupted and checkpointed, then the runner sleeps." % st["current"]["task"])
    else:
        eng.end_session("stopped by owner")
        print("Session stopped; the runner is asleep.")


def cmd_status(eng, a):
    st = eng.state()
    data = eng.tasks()
    alive = daemon_alive(eng)
    print("Mode:        %s%s" % (st["mode"], (" (%s)" % st["pause_reason"]) if st.get("pause_reason") else ""))
    print("Daemon:      %s" % ("running, pid %d, heartbeat %ss ago" % (alive[0], int(alive[1] or 0)) if alive else "NOT running"))
    if st.get("session"):
        print("Session:     until %s (%s left)" % (hm(st["session"]["end"]), fmt_dur(st["session"]["end"] - now())))
    if st.get("wait"):
        w = st["wait"]
        print("Waiting:     %s until %s — %s" % (w["reason"], hm(w["until"]), w.get("detail", "")))
    blocks = {m: u for m, u in (st.get("model_blocks") or {}).items() if u > now()}
    if blocks:
        print("Model limit: %s" % ", ".join("%s until %s" % (m, hm(u)) for m, u in blocks.items()))
    cur = st.get("current")
    if cur:
        print("Current:     %s on %s, running %s" % (cur["task"], cur["model"], fmt_dur(now() - cur["started"])))
    print("Unit:        %s (done: %s)" % (data.get("unit"), ", ".join(data.get("units_done", [])) or "none"))
    counts = {}
    for t in data["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    print("Tasks:       %s" % (", ".join("%s %d" % kv for kv in sorted(counts.items())) or "none yet"))
    print("Next:        %s" % (st.get("next_action") or "-"))
    cp = st.get("last_checkpoint")
    if cp:
        print("Checkpoint:  %s — %s %s" % (iso(cp["at"]), cp["why"], ", ".join(cp.get("commits", []))))
    blocked = [t for t in data["tasks"] if t["status"] == "blocked"]
    for t in blocked:
        b = t.get("blocked") or {}
        print("Blocked:     %s — %s %s %s" % (t["id"], b.get("reason"), b.get("waiting_for") or "", (b.get("note") or "")[:80]))


def cmd_tasks(eng, a):
    data = eng.tasks()
    for t in data["tasks"]:
        if not a.all and t["status"] == "done":
            continue
        model = t.get("model") or (eng.cfg.model_for(t["kind"]) if t["kind"] in MODEL_KINDS else "-")
        b = t.get("blocked") or {}
        print("%-28s %-12s %-9s %-7s %s%s" % (t["id"], t["kind"], t["status"], model, t.get("title", "")[:60],
                                               ("  [%s]" % b.get("reason")) if b else ""))


def cmd_log(eng, a):
    path = os.path.join(eng.cfg.var_dir, "logs", "runner.log")
    if not os.path.exists(path):
        print("no log yet")
        return
    if a.follow:
        os.execvp("tail", ["tail", "-n", str(a.n), "-f", path])
    lines = open(path, encoding="utf-8").read().splitlines()
    print("\n".join(lines[-a.n:]))


def cmd_usage(eng, a):
    path = eng.store.path("usage.jsonl")
    if not os.path.exists(path):
        print("no runs yet")
        return
    since = now() - a.days * 86400
    per = {}
    for line in open(path):
        r = json.loads(line)
        if r["ts"] < since:
            continue
        m = per.setdefault(r["model"], {"runs": 0, "cost": 0.0, "in": 0, "out": 0, "cache": 0})
        m["runs"] += 1
        m["cost"] += r.get("cost_usd_estimate") or 0
        u = r.get("usage") or {}
        m["in"] += u.get("input_tokens", 0)
        m["out"] += u.get("output_tokens", 0)
        m["cache"] += u.get("cache_read_input_tokens", 0)
    print("Last %d day(s). Costs are client-side estimates; on a subscription they show relative use, not a bill." % a.days)
    print("%-8s %5s %10s %12s %12s %14s" % ("model", "runs", "est. $", "input tok", "output tok", "cache-read tok"))
    for m, v in sorted(per.items()):
        print("%-8s %5d %10.2f %12d %12d %14d" % (m, v["runs"], v["cost"], v["in"], v["out"], v["cache"]))
    print("Remaining subscription allowance is not readable by scripts; use /usage in an interactive session.")


def cmd_unblock(eng, a):
    with eng.store.locked():
        data = eng.tasks()
        for t in data["tasks"]:
            if t["id"] == a.task:
                t["status"] = "pending"
                t["blocked"] = None
                if a.note:
                    t.setdefault("notes", []).append("Owner: " + a.note)
                if a.model:
                    t["model"] = a.model
                eng.save_tasks(data)
                eng.log("owner unblocked %s" % a.task)
                print("%s is pending again." % a.task)
                return
    print("no such task")
    return 1


def cmd_retry(eng, a):
    a.note = a.note or "retry requested by the owner"
    return cmd_unblock(eng, a)


def cmd_doctor(eng, a):
    cfg = eng.cfg
    ok = True

    def line(good, what, detail=""):
        nonlocal ok
        ok = ok and good
        print("%s %-34s %s" % ("✓" if good else "✗", what, detail))

    line(sys.version_info >= (3, 9), "python >= 3.9", platform.python_version())
    cli = cfg.agent_cli
    line(bool(cli and shutil.which(cli)), "agent CLI found", cli or "set [agent] cli_path in ~/.skysoft/ems-runner.ini")
    if cli and shutil.which(cli):
        v = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout.strip()
        line(True, "agent CLI version", v)
        if a.live:
            print("  live check (one tiny haiku run)…")
            p = subprocess.run([cli, "-p", "Reply with the single word ready.", "--model", "haiku", "--max-turns", "1",
                                "--output-format", "json"], capture_output=True, text=True, timeout=180)
            good = p.returncode == 0 and "ready" in p.stdout.lower()
            line(good, "login and headless run", "ok" if good else (p.stdout + p.stderr)[-200:])
    line(os.path.isdir(cfg.project_root), "project root", cfg.project_root)
    line(os.path.exists(os.path.join(cfg.project_root, "docs", "collab")), "docs/collab present")
    st = shutil.disk_usage(cfg.project_root if os.path.isdir(cfg.project_root) else "/")
    line(st.free > cfg.getint("doctor", "min_free_gb", 40) * 2 ** 30, "free disk", "%d GB" % (st.free // 2 ** 30))
    if shutil.which("docker"):
        p = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True)
        line(p.returncode == 0, "docker running", ("%.1f GB for Docker" % (int(p.stdout.strip()) / 2 ** 30)) if p.returncode == 0 and p.stdout.strip().isdigit() else p.stderr.strip()[:80])
    else:
        line(False, "docker installed")
    sec = expand("~/.skysoft/secrets.env")
    if os.path.exists(sec):
        mode = os.stat(sec).st_mode & 0o777
        line(mode & 0o077 == 0, "secrets file private", "%s mode %o" % (sec, mode))
        s = load_secrets()
        line(bool(s.get("TELEGRAM_BOT_TOKEN") and s.get("TELEGRAM_CHAT_ID")), "telegram configured")
        line(bool(s.get("NTFY_TOPIC")) and len(s.get("NTFY_TOPIC", "")) >= 24, "ntfy topic (long, random)")
    else:
        line(False, "secrets file", "%s missing" % sec)
    for tool, why in (("git", "checkpoints"), ("caffeinate", "keep awake"), ("osascript", "macOS notifications")):
        line(bool(shutil.which(tool)), tool, why)
    if a.mobile:
        for tool in ("flutter", "adb", "emulator", "xcrun", "pod"):
            line(bool(shutil.which(tool)), "mobile: %s" % tool)
    line(bool(daemon_alive(eng)), "runner daemon alive", "use `ems install`" if not daemon_alive(eng) else "")
    line(os.path.exists(PLIST), "background service installed", PLIST)
    return 0 if ok else 1


def cmd_install(eng, a):
    tpl = open(os.path.join(RUNNER_HOME, "launchd", "%s.plist.template" % LABEL)).read()
    out = tpl.replace("{PYTHON}", sys.executable).replace("{RUNNER_HOME}", RUNNER_HOME).replace(
        "{VAR}", eng.cfg.var_dir).replace("{PATH}", os.environ.get("PATH", "/usr/bin:/bin"))
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    with open(PLIST, "w") as f:
        f.write(out)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (uid, LABEL)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", "gui/%d" % uid, PLIST], check=False)
    print("Background service installed: %s" % PLIST)


def cmd_uninstall(eng, a):
    subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), LABEL)], check=False)
    if os.path.exists(PLIST):
        os.rename(PLIST, PLIST + ".disabled")
    print("Background service removed (plist renamed to .disabled).")


def cmd_notify_test(eng, a):
    ch = notify.send(eng.cfg, eng.log, a.level, "Test notification", "If you can read this, %s works." % a.level)
    print("sent via: %s" % (", ".join(ch) or "nothing — check ~/.skysoft/secrets.env"))


def cmd_telegram_chat_id(eng, a):
    token = load_secrets().get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Put TELEGRAM_BOT_TOKEN in ~/.skysoft/secrets.env first, then send any message to your bot.")
        return 1
    import urllib.error
    try:
        ids = notify.telegram_find_chat_id(token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            print("Telegram rejected the bot token (HTTP %d). It is wrong, incomplete or revoked: copy the newest "
                  "token from @BotFather and write it again to ~/.skysoft/secrets.env." % e.code)
        else:
            print("Telegram error HTTP %d; try again in a minute." % e.code)
        return 1
    except OSError as e:
        print("Cannot reach Telegram: %s" % type(e).__name__)
        return 1
    if not ids:
        print("The token works, but no messages yet: send your bot any message in Telegram, then run this again.")
        return 1
    print("Chat id(s) that messaged the bot: %s" % ", ".join(str(i) for i in ids))
    print("Add it with:  echo 'TELEGRAM_CHAT_ID=%s' >> ~/.skysoft/secrets.env" % ids[0])


def cmd_selftest(eng, a):
    test = os.path.join(RUNNER_HOME, "tests", "selftest.py")
    print("Running the sandbox tests with a fake agent (no usage). This takes about 1-2 minutes;\n"
          "a few tests deliberately wait 3-30 seconds. Please let it finish.\n", flush=True)
    return subprocess.call([sys.executable, "-W", "ignore", test, "-v"])


def cmd_progress(eng, a):
    eng.write_progress()
    print(open(eng.store.path("PROGRESS.md")).read())


def cmd_watch(eng, a):
    """Readable live view of the current run: which files it reads, edits and which commands it runs."""
    import glob as _g
    shown = None
    pos = 0
    try:
        while True:
            st = eng.state()
            cur = st.get("current")
            path = cur and cur.get("log")
            if not path:
                runs = sorted(_g.glob(os.path.join(eng.cfg.var_dir, "logs", "runs", "*.jsonl")))
                path = runs[-1] if runs else None
            if path != shown:
                shown, pos = path, 0
                print("\n=== %s ===" % (os.path.basename(path) if path else "no run yet"), flush=True)
            if path and os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    for line in f:
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        if ev.get("type") == "assistant":
                            for b in (ev.get("message") or {}).get("content") or []:
                                if b.get("type") == "tool_use":
                                    i = b.get("input") or {}
                                    what = i.get("command") or i.get("file_path") or i.get("pattern") or i.get("url") or i.get("description") or ""
                                    print("  %-8s %s" % (b.get("name"), str(what).replace("\n", " ")[:150]), flush=True)
                                elif b.get("type") == "text" and b.get("text", "").strip():
                                    print("  · " + b["text"].strip().replace("\n", " ")[:200], flush=True)
                        elif ev.get("type") == "result":
                            print("  ✔ run finished: %s" % (ev.get("result") or "")[-200:].replace("\n", " "), flush=True)
                    pos = f.tell()
            if not a.follow:
                return
            time.sleep(2)
    except KeyboardInterrupt:
        return


def cmd_open(eng, a):
    root = eng.cfg.project_root
    target = {"screens": os.path.join(root, "qa", "out"), "designs": os.path.join(root, "docs", "design"),
              "reviews": os.path.join(root, "docs", "reviews"), "approvals": os.path.join(root, "docs", "collab", "approvals"),
              "progress": eng.store.path("PROGRESS.md")}[a.what]
    if a.what == "progress":
        eng.write_progress()
    subprocess.run(["open", target], check=False)
    print("opened %s" % target)


def cmd_review(eng, a):
    from .review import build
    from .plan import unit_by_id
    u = unit_by_id(eng.seq, a.unit)
    if not u:
        print("unknown group %s" % a.unit)
        return 1
    out = build(eng.cfg.project_root, u["phases"], a.unit)
    print("review page: %s" % out)
    if not a.no_open:
        subprocess.run(["open", out], check=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ems", description="Control the EMS development runner.")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("init", help="first-time setup: plan baseline and message marks")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("start", help="start a working session (default from config, 5h)")
    s.add_argument("--for", dest="duration", help="e.g. 5h, 90m, 1h30m")
    s.add_argument("--until", help="local time, e.g. 06:00")
    s.set_defaults(fn=cmd_start)
    s = sub.add_parser("set-duration", help="change the running session: 3h (from its start) or 07:30")
    s.add_argument("duration")
    s.set_defaults(fn=cmd_set_duration)
    s = sub.add_parser("pause", help="pause after the current task (--now: interrupt and checkpoint)")
    s.add_argument("--now", action="store_true")
    s.set_defaults(fn=cmd_pause)
    sub.add_parser("resume", help="resume a paused session").set_defaults(fn=cmd_resume)
    sub.add_parser("stop", help="end the session now (checkpoint, then sleep)").set_defaults(fn=cmd_stop)
    sub.add_parser("status", help="mode, session, current task, next action, blocked tasks").set_defaults(fn=cmd_status)
    s = sub.add_parser("tasks", help="pending/blocked tasks (--all for everything)")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_tasks)
    s = sub.add_parser("log", help="runner log")
    s.add_argument("-n", type=int, default=40)
    s.add_argument("-f", "--follow", action="store_true")
    s.set_defaults(fn=cmd_log)
    s = sub.add_parser("usage", help="runs, estimated cost and tokens per model")
    s.add_argument("--days", type=int, default=1)
    s.set_defaults(fn=cmd_usage)
    for name, fn in (("unblock", cmd_unblock), ("retry", cmd_retry)):
        s = sub.add_parser(name, help="%s a blocked or failed task" % name)
        s.add_argument("task")
        s.add_argument("--note")
        s.add_argument("--model", choices=["haiku", "sonnet", "opus"])
        s.set_defaults(fn=fn)
    s = sub.add_parser("doctor", help="check tools, login, disk, docker, notifications")
    s.add_argument("--live", action="store_true", help="also do one tiny real haiku run")
    s.add_argument("--mobile", action="store_true", help="also check the Flutter/Android/iOS tools")
    s.set_defaults(fn=cmd_doctor)
    sub.add_parser("install", help="install the macOS background service").set_defaults(fn=cmd_install)
    sub.add_parser("uninstall", help="remove the background service").set_defaults(fn=cmd_uninstall)
    s = sub.add_parser("notify-test", help="send a test notification")
    s.add_argument("--level", choices=["info", "urgent"], default="urgent")
    s.set_defaults(fn=cmd_notify_test)
    sub.add_parser("telegram-chat-id", help="find your Telegram chat id").set_defaults(fn=cmd_telegram_chat_id)
    s = sub.add_parser("selftest", help="run the sandbox tests (fake agent, no usage)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_selftest)
    sub.add_parser("progress", help="print the progress table").set_defaults(fn=cmd_progress)
    s = sub.add_parser("watch", help="live, readable view of what the current run is doing")
    s.add_argument("--once", dest="follow", action="store_false")
    s.set_defaults(fn=cmd_watch, follow=True)
    s = sub.add_parser("review", help="one review page for a group: problems, one picture per screen, key variants")
    s.add_argument("unit")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("open", help="open screenshots, designs, reviews, approvals or progress in Finder")
    s.add_argument("what", choices=["screens", "designs", "reviews", "approvals", "progress"])
    s.set_defaults(fn=cmd_open)
    sub.add_parser("daemon", help="run the daemon in the foreground (launchd uses this)").set_defaults(
        fn=lambda eng, a: eng.daemon())
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    eng = Engine(Config())
    return a.fn(eng, a) or 0


if __name__ == "__main__":
    sys.exit(main())
