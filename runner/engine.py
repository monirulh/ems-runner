# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# The runner daemon: session clock, task queue, gates, limits, recovery and checkpoints.

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import glob
import time

from . import agent, notify
from .common import (DEFAULT_STATE, Config, Logger, RUNNER_HOME, Store, fmt_dur, hm, iso, now, read_text,
                     write_json)
from .plan import Project, load_sequence, msg_id, msg_refs, next_unit, unit_by_id

MODEL_KINDS = ("group_design", "design_check", "phase_plan", "build", "test", "apply", "qa", "docs", "po", "handover",
               "review", "fix", "merge", "inbox", "skyui")
PLAN_TASK_KINDS = ("build", "test", "apply", "qa", "docs")
NO_MODEL = ("gate", "wait_cards", "phase_end", "unit_end")
ESCALATE_TO = "opus"


class SafeDict(dict):
    def __missing__(self, key):
        return ""


def _sig(text):
    norm = " ".join((text or "").lower().split())[:400]
    return hashlib.sha1(norm.encode()).hexdigest()[:10]


class Engine(object):
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.store = Store(self.cfg.var_dir)
        self.log = Logger(self.cfg.var_dir)
        self.project = Project(self.cfg)
        self.seq = load_sequence(self.cfg)
        self.caffeinate = None
        self.last_tick = now()

    # ------------------------------------------------------------------ state
    def state(self):
        st = dict(DEFAULT_STATE)
        st.update(self.store.load("state.json", {}))
        return st

    def update_state(self, **changes):
        with self.store.locked():
            st = self.state()
            st.update(changes)
            self.store.save("state.json", st)
            return st

    def tasks(self):
        return self.store.load("tasks.json", {"unit": None, "units_done": [], "tasks": [], "exceptions": []})

    def save_tasks(self, data):
        self.store.save("tasks.json", data)

    def notify(self, level, title, body="", once_key=None):
        if once_key:
            with self.store.locked():
                st = self.state()
                if once_key in st["notified"]:
                    return
                st["notified"] = (st["notified"] + [once_key])[-200:]
                self.store.save("state.json", st)
        notify.send(self.cfg, self.log, level, title, body)

    # ------------------------------------------------------------------ init
    def init(self):
        """Mark existing messages as seen and record the plan baseline."""
        with self.store.locked():
            st = self.state()
            for box in ("to-dev", "to-skyui", "to-research"):
                st["seen"][box] = self.project.list_messages(box)
            st["baseline"] = {"master_sha": self.project.master_sha(), "at": now()}
            self.store.save("state.json", st)
            if not os.path.exists(self.store.path("tasks.json")):
                self.save_tasks(self.tasks())
        self.log("init: baseline recorded, %d existing messages marked as pre-runner" %
                 sum(len(v) for v in st["seen"].values()))
        return st

    # ------------------------------------------------------------------ session control (used by CLI)
    def start_session(self, seconds=None, until=None):
        hours = self.cfg.getfloat("session", "default_hours", 5)
        end = until or (now() + (seconds if seconds else hours * 3600))
        st = self.update_state(mode="running", pause_reason=None, session={"start": now(), "end": end},
                               wait=None, interrupt=None, service_failures=0, notified=[])
        self.log("session started until %s" % iso(end), kind="session_start", end=end)
        return st

    # ------------------------------------------------------------------ daemon loop
    def daemon(self):
        pidfile = self.store.path("daemon.pid")
        lockfd = os.open(self.store.path("daemon.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        import fcntl
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # another daemon runs: wait quietly and take over if it ever stops (no restart loop)
            fcntl.flock(lockfd, fcntl.LOCK_EX)
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))
        self._stop = False

        def on_term(*_a):
            # never leave an agent run orphaned: interrupt it, checkpoint, then exit
            if self.state().get("current"):
                self._stop = True
                self.update_state(interrupt="restart")
            else:
                self._shutdown("SIGTERM")
        signal.signal(signal.SIGTERM, on_term)
        self.log("daemon started (pid %d)" % os.getpid())
        self.recover()
        poll = self.cfg.getint("daemon", "poll_seconds", 10)
        once = os.environ.get("EMS_RUNNER_ONCE")
        while True:
            t = now()
            if t - self.last_tick > max(120, poll * 6):
                self.log("clock gap of %s detected (Mac slept?); reconciling" % fmt_dur(t - self.last_tick))
                self.recover()
            self.last_tick = t
            try:
                worked = self.step()
            except Exception as e:  # noqa: BLE001
                import traceback
                self.log("runner error: %s\n%s" % (e, traceback.format_exc()), kind="error")
                self.notify("urgent", "Runner error", str(e)[:300], once_key="err:" + _sig(str(e)))
                worked = False
            if getattr(self, "_stop", False):
                self._shutdown("SIGTERM after checkpoint")
            self.last_tick = now()  # time spent inside a run is not a sleep gap
            try:
                self.maybe_digest()
            except Exception as e:  # noqa: BLE001
                self.log("digest failed: %s" % e)
            self.heartbeat()
            self.manage_caffeinate()
            if once and not worked:
                return 0
            if not worked:
                time.sleep(poll)

    def _shutdown(self, why):
        self.log("daemon stopping (%s)" % why)
        if self.caffeinate:
            self.caffeinate.terminate()
        sys.exit(0)

    def heartbeat(self):
        with open(self.store.path("heartbeat"), "w") as f:
            f.write(str(now()))

    def manage_caffeinate(self):
        st = self.state()
        want = st["mode"] in ("running", "waiting") and self.cfg.getbool("daemon", "keep_awake", True)
        if want and not self.caffeinate and shutil.which("caffeinate"):
            self.caffeinate = subprocess.Popen(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())])
        elif not want and self.caffeinate:
            self.caffeinate.terminate()
            self.caffeinate = None

    # ------------------------------------------------------------------ recovery
    def recover(self):
        """Never assume an interrupted task finished: put it back with a note."""
        st = self.state()
        cur = st.get("current")
        if not cur:
            return
        pid = cur.get("pid")
        alive = False
        if pid == os.getpid():
            return
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if alive:
            return
        with self.store.locked():
            data = self.tasks()
            for t in data["tasks"]:
                if t["id"] == cur["task"] and t["status"] == "running":
                    t["status"] = "pending"
                    t.setdefault("notes", []).append(
                        "%s: a previous attempt was interrupted (runner restart, crash or sleep). Inspect git "
                        "status, diffs and test results first; do not assume any step finished." % iso())
            self.save_tasks(data)
            st = self.state()
            st["current"] = None
            self.store.save("state.json", st)
        self.log("recovered interrupted task %s" % cur["task"], kind="recover", task=cur["task"])
        self.checkpoint("recovery after interruption", cur["task"])

    # ------------------------------------------------------------------ one step
    def step(self):
        st = self.state()
        t = now()
        if st["mode"] == "waiting":
            w = st.get("wait") or {}
            if w.get("reason") == "network" and t < w.get("until", 0):
                if self.net_ok():
                    self.update_state(mode="running", wait=None)
                    self.log("network is back")
                return False
            if t < w.get("until", 0):
                if st.get("session") and t >= st["session"]["end"]:
                    self.end_session("session time ended while waiting (%s)" % w.get("reason"))
                return False
            if st.get("session") and t >= st["session"]["end"]:
                self.end_session("session time ended while waiting (%s)" % w.get("reason"))
                return False
            self.update_state(mode="running", wait=None)
            self.log("wait over (%s); resuming" % w.get("reason"))
            self.notify("info", "Resumed", "Wait for %s is over." % w.get("reason"))
            st = self.state()
        if st["mode"] != "running":
            return False
        if not st.get("session") or t >= st["session"]["end"]:
            self.end_session("session time ended")
            return False

        self.scan_messages()
        self.refresh_blocked()
        self.ensure_unit()
        task, wait_reason = self.pick()
        if not task:
            if wait_reason:
                self.update_state(next_action=wait_reason)
                if all((t.get("blocked") or {}).get("reason") in ("approval", "card-missing")
                       for t in self.tasks()["tasks"] if t["status"] == "blocked"):
                    return False  # each gate already sent its own notice
                self.notify("urgent" if "approval" in wait_reason or "owner" in wait_reason or "research" in wait_reason else "info",
                            "Waiting", wait_reason, once_key="idle:" + _sig(wait_reason))
            return False
        self.execute(task)
        return True

    # ------------------------------------------------------------------ units and tasks
    def _task(self, tid, kind, unit, **kw):
        t = {"id": tid, "kind": kind, "unit": unit, "phase": kw.pop("phase", None), "title": kw.pop("title", tid),
             "deps": kw.pop("deps", []), "status": kw.pop("status", "pending"), "model": kw.pop("model", None),
             "attempts": [], "blocked": kw.pop("blocked", None), "verify": kw.pop("verify", []),
             "notes": kw.pop("notes", []), "priority": kw.pop("priority", 0), "created": now()}
        t.update(kw)
        return t

    def ensure_unit(self):
        with self.store.locked():
            data = self.tasks()
            cur = data.get("unit")
            if cur:
                end = [t for t in data["tasks"] if t["id"] == cur + ".end"]
                if not end or end[0]["status"] != "done":
                    return
                data["units_done"].append(cur)
            nxt = next_unit(self.seq, cur) if cur else self.seq["units"][0]
            if nxt is None:
                if cur:
                    data["unit"] = None
                    self.save_tasks(data)
                    self.notify("urgent", "Approved plan complete", "All units in the sequence are done. "
                                "Research can now consolidate the backlog.")
                return
            # plan baseline check before a new unit starts
            st = self.state()
            sha = self.project.master_sha()
            if st.get("baseline") and sha and sha != st["baseline"].get("master_sha"):
                if st.get("correction_seen_since_baseline"):
                    st["baseline"] = {"master_sha": sha, "at": now()}
                    st["correction_seen_since_baseline"] = False
                    self.store.save("state.json", st)
                    self.log("plan master changed with a labelled correction; new baseline accepted")
                else:
                    self.notify("urgent", "Plan file changed", "The master phase list changed without a labelled "
                                "correction message. The runner will not start %s until Research sends one." % nxt["id"],
                                once_key="baseline:" + sha[:10])
                    return
            uid = nxt["id"]
            data["unit"] = uid
            tasks = []
            prev = None
            missing = self.project.missing_cards(nxt)
            if missing:
                tasks.append(self._task(uid + ".cards", "wait_cards", uid, status="blocked",
                                        blocked={"reason": "card-missing", "waiting_for": missing, "since": now()},
                                        title="Cards from Research for %s" % ", ".join(missing)))
                prev = uid + ".cards"
            if nxt.get("design", True):
                tasks.append(self._task(uid + ".design", "group_design", uid, deps=[prev] if prev else [],
                                        title="Group design %s" % uid))
                gate_dep = uid + ".design"
                if self.cfg.getbool("auto", "design", False):
                    tasks.append(self._task(uid + ".design_check", "design_check", uid, deps=[uid + ".design"],
                                            title="Research check of the %s design (AUTO-Q10)" % uid))
                    gate_dep = uid + ".design_check"
                tasks.append(self._task(uid + ".gate.design", "gate", uid, deps=[gate_dep], status="pending",
                                        gate=nxt["gates"]["design"], title="Owner approval: %s" % nxt["gates"]["design"]))
                prev = uid + ".gate.design"
            for ph in nxt["phases"]:
                tasks.append(self._task(ph + ".plan", "phase_plan", uid, phase=ph, deps=[prev] if prev else [],
                                        title="Plan tasks for %s" % ph))
                tasks.append(self._task(ph + ".end", "phase_end", uid, phase=ph, deps=[ph + ".plan"],
                                        title="%s complete" % ph))
                prev = ph + ".end"
            tasks.append(self._task(uid + ".review", "review", uid, deps=[prev], title="Independent review %s" % uid,
                                    round=1))
            tasks.append(self._task(uid + ".gate.release", "gate", uid, deps=[uid + ".review"],
                                    gate=nxt["gates"]["release"], title="Owner approval: %s" % nxt["gates"]["release"]))
            tasks.append(self._task(uid + ".merge", "merge", uid, deps=[uid + ".gate.release"],
                                    title="Merge and tag %s" % uid))
            tasks.append(self._task(uid + ".end", "unit_end", uid, deps=[uid + ".merge"], title="%s released" % uid))
            data["tasks"].extend(tasks)
            # approved exceptions enter only here, at a group boundary
            pend = []
            for ex in data.get("exceptions", []):
                if self.project.approval_ok("EXC_%s.md" % msg_id(ex)):
                    pend.append(ex)
            if pend:
                data["tasks"].append(self._task("inbox-exc-%d" % int(now()), "inbox", uid, priority=10,
                                                messages=pend, title="Apply approved exception(s)"))
                data["exceptions"] = [e for e in data["exceptions"] if e not in pend]
            self.save_tasks(data)
        self.log("unit %s materialized (%d tasks)" % (uid, len(tasks)), kind="unit", unit=uid)
        self.notify("info", "Starting %s" % uid, "Phases: %s" % ", ".join(nxt["phases"]))

    def pick(self):
        st = self.state()
        data = self.tasks()
        done = {t["id"] for t in data["tasks"] if t["status"] == "done"}
        blocks = {m: u for m, u in (st.get("model_blocks") or {}).items() if u > now()}
        eligible = []
        for i, t in enumerate(data["tasks"]):
            if t["status"] != "pending":
                continue
            if any(d not in done for d in t["deps"]):
                continue
            eligible.append((-t.get("priority", 0), i, t))
        eligible.sort(key=lambda x: (x[0], x[1]))
        limited = []
        for _, _, t in eligible:
            if t["kind"] in ("gate", "wait_cards"):
                continue
            if t["kind"] in NO_MODEL:
                return t, None
            model = t.get("model") or self.cfg.model_for(t["kind"])
            if model in blocks:
                limited.append((blocks[model], model))
                continue
            return t, None
        if limited:
            until = min(u for u, _ in limited)
            self.update_state(mode="waiting", wait={"reason": "limit", "until": until + 300,
                                                    "detail": "%s limit" % limited[0][1]})
            return None, None
        blocked = [t for t in data["tasks"] if t["status"] == "blocked"]
        if blocked:
            parts = []
            for t in blocked[:4]:
                b = t.get("blocked") or {}
                parts.append("%s: %s%s" % (t["id"], b.get("reason"),
                                           (" (" + str(b.get("waiting_for")) + ")") if b.get("waiting_for") else ""))
            return None, "blocked — " + "; ".join(parts)
        return None, None

    # ------------------------------------------------------------------ messages and gates
    def scan_messages(self):
        st = self.state()
        new = {}
        for box in ("to-dev", "to-skyui", "to-research"):
            have = set(st["seen"].get(box, []))
            new[box] = [n for n in self.project.list_messages(box) if n not in have]
        if not any(new.values()):
            return
        with self.store.locked():
            data = self.tasks()
            st = self.state()
            inbox_msgs, backlog = [], []
            for name in new["to-dev"]:
                m = self.project.read_message("to-dev", name)
                kind = m["kind"]
                if kind in ("correction", "answer", "card"):
                    inbox_msgs.append(name)
                    if kind == "correction":
                        st["correction_seen_since_baseline"] = True
                    self._unblock_by_refs(data, m)
                elif kind == "exception":
                    data.setdefault("exceptions", []).append(name)
                    self.notify("urgent", "Exception received", "%s waits for approvals/EXC_%s.md and enters at the "
                                "next group boundary." % (name, msg_id(name)))
                elif kind == "refusal":
                    inbox_msgs.append(name)
                    self.notify("urgent", "SkyUI refused a request", "%s — Research must decide the approach." % name)
                elif kind == "notice":
                    pass
                else:
                    backlog.append(name)
                    self._to_backlog(name, kind)
            if inbox_msgs:
                existing = [t for t in data["tasks"] if t["kind"] == "inbox" and t["status"] == "pending"]
                if existing:
                    existing[0].setdefault("messages", []).extend(inbox_msgs)
                    existing[0].setdefault("deferred", []).extend(backlog)
                else:
                    data["tasks"].append(self._task("inbox-%d" % int(now()), "inbox", data.get("unit"), priority=10,
                                                    messages=inbox_msgs, deferred=backlog,
                                                    title="Read inbox: %s" % ", ".join(inbox_msgs)))
            elif backlog:
                with open(os.path.join(self.project.collab, ".last_read_dev"), "w") as f:
                    f.write(sorted(new["to-dev"])[-1] + "\n")
            for name in new["to-skyui"]:
                m = self.project.read_message("to-skyui", name)
                frm = m["head"].get("from", "").lower()
                if frm.startswith("dev") and m["head"].get("needs action", "yes").lower().startswith("y"):
                    data["tasks"].append(self._task("skyui-%s" % msg_id(name), "skyui", data.get("unit"), priority=5,
                                                    request=name, title="SkyUI request %s" % name))
            for name in new["to-research"]:
                m = self.project.read_message("to-research", name)
                blocking = m["head"].get("blocking", "").lower().startswith("y")
                self.notify("urgent" if blocking else "info", "Message for Research",
                            "%s (from %s)%s" % (name, m["head"].get("from", "?"),
                                                " — blocking; say 'next' in the Research chat" if blocking else ""))
            for box in new:
                st["seen"][box] = st["seen"].get(box, []) + new[box]
            self.store.save("state.json", st)
            self.save_tasks(data)
        if any(new.values()):
            self.log("messages: %s" % {k: v for k, v in new.items() if v}, kind="messages")

    def _to_backlog(self, name, kind):
        src = os.path.join(self.project.collab, "to-dev", name)
        dst_dir = self.project.p(self.cfg.get("paths", "backlog_dir", "docs/backlog"))
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, os.path.join(dst_dir, name))
        self.log("%s message %s copied to backlog (not in the active plan)" % (kind or "unlabelled", name),
                 kind="backlog", message=name)
        self.notify("info", "Parked in backlog", "%s (%s)" % (name, kind or "no Kind label"))

    def _unblock_by_refs(self, data, msg):
        refs = msg_refs(msg)
        for t in data["tasks"]:
            b = t.get("blocked") or {}
            if t["status"] != "blocked" or b.get("reason") not in ("question", "skyui", "research"):
                continue
            want = str(b.get("waiting_for") or "")
            if want and (msg_id(want) in refs or want in refs or want in msg["text"]):
                t["status"] = "pending"
                t["blocked"] = None
                t.setdefault("notes", []).append("Answer arrived in docs/collab/to-dev/%s — read it first." % msg["name"])
                self.log("unblocked %s by %s" % (t["id"], msg["name"]))

    def refresh_blocked(self):
        with self.store.locked():
            data = self.tasks()
            changed = False
            for t in data["tasks"]:
                if t["kind"] == "gate" and t["status"] in ("pending", "blocked"):
                    deps_done = all(any(x["id"] == d and x["status"] == "done" for x in data["tasks"]) for d in t["deps"])
                    if not deps_done:
                        continue
                    if not self.project.approval_ok(t["gate"]) and t["id"].endswith(".gate.release"):
                        self.try_auto_release(data, t)
                    if self.project.approval_ok(t["gate"]):
                        t["status"] = "done"
                        t["finished"] = now()
                        changed = True
                        self.log("approval found: %s" % t["gate"], kind="gate", gate=t["gate"])
                        self.notify("info", "Approval found", "%s — continuing." % t["gate"])
                    elif t["status"] == "pending":
                        t["status"] = "blocked"
                        t["blocked"] = {"reason": "approval", "waiting_for": t["gate"], "since": now()}
                        changed = True
                        self.notify("urgent", "Waiting for your approval",
                                    "%s. Approve in the Research chat; the runner resumes when Research writes "
                                    "docs/collab/approvals/%s." % (t["title"], t["gate"]), once_key="gate:" + t["gate"])
                elif t["status"] == "blocked":
                    b = t.get("blocked") or {}
                    if t["kind"] == "wait_cards":
                        miss = [p for p in b.get("waiting_for", []) if not os.path.exists(self.project.card_path(p))]
                        if not miss:
                            t["status"] = "done"
                            changed = True
                            self.log("cards present for %s" % t["unit"])
                        else:
                            self.notify("urgent", "Cards needed from Research",
                                        "%s needs cards %s." % (t["unit"], ", ".join(miss)), once_key="cards:" + t["unit"])
                    elif b.get("reason") == "approval" and b.get("waiting_for"):
                        if self.project.approval_ok(os.path.basename(str(b["waiting_for"]))):
                            t["status"] = "pending"
                            t["blocked"] = None
                            t.setdefault("notes", []).append("Owner approval file %s now exists." % b["waiting_for"])
                            changed = True
            if changed:
                self.save_tasks(data)

    # ------------------------------------------------------------------ execution
    def execute(self, task):
        if task["kind"] in ("phase_end", "unit_end"):
            self.mark(task["id"], status="done", finished=now())
            if task["kind"] == "phase_end":
                self.notify("info", "%s finished" % task["phase"], "All tasks of the phase passed verification.")
            return
        if task["kind"] in NO_MODEL:
            return
        model = task.get("model") or self.cfg.model_for(task["kind"])
        prompt, system_file, cwd, role = self.build_prompt(task, model)
        settings = self.guard_settings()
        st = self.state()
        grace = self.cfg.getint("session", "grace_minutes", 15) * 60
        deadline = min(now() + self.cfg.getint("session", "max_task_minutes", 120) * 60,
                       st["session"]["end"] + grace)
        cmd = agent.build_command(self.cfg, prompt, model, self.cfg.max_turns_for(task["kind"]), system_file, settings)
        run_log = os.path.join(self.cfg.var_dir, "logs", "runs", "%s_%s.jsonl" % (time.strftime("%Y%m%d_%H%M%S"), task["id"]))
        started = now()
        self.mark(task["id"], status="running")
        self.update_state(current={"task": task["id"], "model": model, "started": started, "pid": os.getpid(),
                                   "log": run_log}, next_action="running %s on %s" % (task["id"], model))
        self.log("start %s (%s) on %s" % (task["id"], task["kind"], model), kind="task_start", task=task["id"], model=model)

        def should_interrupt():
            s = self.state()
            if s.get("interrupt"):
                return s["interrupt"]
            return None

        out = agent.run(self.cfg, self.log, cmd, cwd, run_log, deadline, should_interrupt,
                        env_extra={"EMS_TASK_ID": task["id"], "EMS_ROLE": role,
                                   "EMS_PROJECT_ROOT": self.project.root})
        ended = now()
        attempt = {"model": model, "start": started, "end": ended, "kind": out.kind, "cost": out.cost,
                   "session_id": out.session_id, "status": (out.result or {}).get("status"),
                   "summary": ((out.result or {}).get("summary") or out.detail or "")[:500], "log": run_log}
        self.record_usage(task, model, out)
        self.update_state(current=None, interrupt=None)
        self.handle(task["id"], out, attempt)

    def build_prompt(self, task, model):
        kind = task["kind"]
        unit = unit_by_id(self.seq, task["unit"]) if task.get("unit") else None
        role = "skyui" if kind == "skyui" else "dev"
        cwd = self.project.p(self.cfg.get("paths", "skyui_dir", "apps/skyui")) if role == "skyui" else self.project.root
        tpl = read_text(os.path.join(RUNNER_HOME, "prompts", "%s.md" % kind))
        notes = "\n".join("- " + n for n in task.get("notes", [])[-6:]) or "- none"
        attempts = task.get("attempts", [])
        last = attempts[-1] if attempts else None
        attempt_note = ""
        if last and (last.get("kind") != "ok" or last.get("status") != "done"):
            attempt_note = "Previous attempt (%s on %s) ended as %s: %s" % (
                iso(last["start"]), last["model"], last.get("status") or last["kind"], last.get("summary", ""))
        fields = SafeDict(
            task_id=task["id"], unit=task.get("unit") or "", phase=task.get("phase") or "",
            phases=", ".join(unit["phases"]) if unit else "", title=task.get("title", ""),
            details=task.get("details", ""), notes=notes, attempt_note=attempt_note or "none",
            card=os.path.relpath(self.project.card_path(task["phase"]), self.project.root) if task.get("phase") else "",
            design_file="docs/design/%s.md" % (task.get("unit") or ""),
            review_file="docs/reviews/%s.md" % (task.get("unit") or ""),
            plan_file=os.path.join(self.cfg.var_dir, "plans", "%s.json" % (task.get("phase") or "")),
            gate_design=(unit or {}).get("gates", {}).get("design", ""),
            gate_release=(unit or {}).get("gates", {}).get("release", ""),
            design_prompt=(unit or {}).get("design_prompt", ""),
            messages="\n".join("- docs/collab/to-dev/%s" % m for m in task.get("messages", [])) or "- none",
            deferred="\n".join("- %s" % m for m in task.get("deferred", [])) or "- none",
            request="docs/collab/to-skyui/%s" % task.get("request", ""),
            items="\n".join("- " + i for i in task.get("items", [])) or "- see the review file",
            round=task.get("round", 1), model=model, project_root=self.project.root,
            verify="\n".join("- `%s`" % v for v in task.get("verify", [])) or "- (none given)",
        )
        prompt = tpl.format_map(fields)
        sys_tpl = read_text(os.path.join(RUNNER_HOME, "prompts", "_system_%s.md" % role))
        sys_path = os.path.join(self.cfg.var_dir, "prompts", "%s.sys.md" % task["id"])
        os.makedirs(os.path.dirname(sys_path), exist_ok=True)
        with open(sys_path, "w", encoding="utf-8") as f:
            f.write(sys_tpl.format_map(fields))
        return prompt, sys_path, cwd, role

    def guard_settings(self):
        if not self.cfg.getbool("agent", "use_guard", True):
            return None
        path = os.path.join(self.cfg.var_dir, "guard_settings.json")
        hook = os.path.join(RUNNER_HOME, "guard", "pretool_guard.py")
        settings = {
            "permissions": {"deny": self.cfg.getlist("guard", "deny_rules")},
            "hooks": {"PreToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|Read",
                                      "hooks": [{"type": "command", "command": "python3 %s" % hook}]}]},
        }
        write_json(path, settings)
        return path

    def record_usage(self, task, model, out):
        rec = {"ts": now(), "task": task["id"], "kind": task["kind"], "model": model, "outcome": out.kind,
               "cost_usd_estimate": out.cost, "usage": out.usage}
        with open(self.store.path("usage.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")

    def mark(self, tid, **changes):
        with self.store.locked():
            data = self.tasks()
            for t in data["tasks"]:
                if t["id"] == tid:
                    t.update(changes)
            self.save_tasks(data)

    # ------------------------------------------------------------------ outcomes
    def handle(self, tid, out, attempt):
        with self.store.locked():
            data = self.tasks()
            task = next(t for t in data["tasks"] if t["id"] == tid)
            task["attempts"].append(attempt)
            self.save_tasks(data)
        k = out.kind
        if k == "ok":
            res = out.result or {}
            status = res.get("status")
            if status == "done":
                problem = self.verify(task, attempt)
                if problem:
                    return self.fail(tid, problem)
                self.after_done(tid, res)
                return
            if task["kind"] == "design_check" and status in ("approve", "owner"):
                problem = self.verify(task, attempt)
                if problem:
                    return self.fail(tid, problem)
                self.after_done(tid, res)
                return self.after_design_check(task, res)
            if status == "changes_required" and task["kind"] == "review":
                return self.review_changes(tid, res)
            if status in ("blocked", "needs_approval", "refused"):
                return self.block(tid, res)
            if status == "failed":
                return self.fail(tid, res.get("summary") or "agent reported failure")
            return self.fail(tid, "run ended without a valid EMS_RESULT line")
        if k == "limit":
            reset = out.reset_at or (now() + self.cfg.getint("limits", "poll_minutes", 30) * 60)
            margin = self.cfg.getint("limits", "resume_margin_minutes", 5) * 60
            self.mark(tid, status="pending", notes=self._notes(tid, "%s: stopped by a usage limit; partial work may "
                                                                    "exist — inspect before continuing." % iso()))
            self.checkpoint("usage limit", tid)
            scope = out.limit_scope or "session"
            if scope in ("opus", "sonnet", "haiku"):
                with self.store.locked():
                    st = self.state()
                    st["model_blocks"][scope] = reset + margin
                    self.store.save("state.json", st)
                msg = "%s limit until %s; other models continue." % (scope, hm(reset))
            else:
                self.update_state(mode="waiting", wait={"reason": "limit", "until": reset + margin,
                                                        "detail": out.detail})
                msg = "%s. Sleeping until %s (if the session still has time)." % (out.detail, hm(reset + margin))
            self.log("usage limit: " + msg, kind="limit", reset=reset)
            self.notify("info", "Usage limit reached", msg)
            return
        if k == "auth":
            self.mark(tid, status="pending")
            self.update_state(mode="paused", pause_reason="auth: " + out.detail)
            self.checkpoint("authentication problem", tid)
            self.notify("urgent", "Login needed", "%s. Run /login in a terminal session, then `ems resume`." % out.detail)
            return
        if k == "network":
            self.mark(tid, status="pending")
            self.update_state(mode="waiting", wait={"reason": "network", "until": now() + 6 * 3600,
                                                    "detail": out.detail})
            self.notify("info", "Network problem", "%s — waiting for the connection." % out.detail,
                        once_key="net:%d" % int(now() // 3600))
            return
        if k == "service":
            st = self.state()
            n = st.get("service_failures", 0) + 1
            backoff = [int(x) for x in self.cfg.getlist("limits", "service_backoff_minutes", ["5", "15", "30"])]
            if n <= len(backoff):
                self.mark(tid, status="pending")
                self.update_state(mode="waiting", service_failures=n,
                                  wait={"reason": "service", "until": now() + backoff[n - 1] * 60, "detail": out.detail})
                self.log("service error (%s); retry in %d min" % (out.detail, backoff[n - 1]))
            else:
                self.update_state(service_failures=0)
                self.block(tid, {"reason": "service", "summary": "service errors kept failing: %s" % out.detail})
            return
        st0 = self.state()
        session_over = not st0.get("session") or now() >= st0["session"]["end"]
        if k == "timeout" and not session_over:
            return self.fail(tid, "run exceeded max_task_minutes; split the work or finish faster")
        if k in ("interrupted", "timeout"):
            self.mark(tid, status="pending", notes=self._notes(tid, "%s: run was stopped (%s); continue from the "
                                                                    "partial work after checking it." % (iso(), out.detail)))
            self.checkpoint("run stopped: %s" % out.detail, tid)
            st = self.state()
            if out.detail in ("stop", "session end") or (st.get("session") and now() >= st["session"]["end"]):
                self.end_session("session time ended" if out.detail != "stop" else "stopped by owner")
            elif out.detail.startswith("pause"):
                self.update_state(mode="paused", pause_reason="owner")
            return
        return self.fail(tid, out.detail or "agent crashed")

    def _notes(self, tid, note):
        data = self.tasks()
        t = next(x for x in data["tasks"] if x["id"] == tid)
        return (t.get("notes") or []) + [note]

    def update_state_service_ok(self):
        if self.state().get("service_failures"):
            self.update_state(service_failures=0)

    # ------------------------------------------------------------------ AUTO-Q10 automatic approvals
    def write_auto_approval(self, unit, kind, evidence):
        name = "%s_%s.md" % (unit, kind)
        path = self.project.approval_path(name)
        if os.path.exists(path):
            return name
        owner = self.cfg.get("project", "owner_name", "Monirul Hasan")
        u = unit_by_id(self.seq, unit) or {"phases": []}
        text = ("Copyright (c) 2026 Monirul Hasan. All rights reserved.\n"
                "<!-- Automatic %s approval written by the EMS runner under AUTO-Q10. -->\n\n"
                "Group %s — %s      Date %s\n"
                "Approved by: %s — automatic under AUTO-Q10 (owner decision 2026-11-25); the owner reviews it afterwards\n"
                "Phases: %s\n"
                "Evidence:\n%s\n"
                "Undo: tell Research \"revoke %s %s\"; a revoked design goes back to G1 for that group, "
                "a revoked release is reverted to the previous tag.\n") % (
            kind, unit, kind, iso()[:10], owner, ", ".join(u["phases"]),
            "\n".join("- " + e for e in evidence), unit, kind)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with open(self.project.approval_path("AUTO_LOG.md"), "a", encoding="utf-8") as f:
            f.write("- %s %s %s — %s\n" % (iso(), unit, kind, "; ".join(evidence)[:300]))
        self.log("automatic %s approval written: %s" % (kind, name), kind="auto_approval", unit=unit, approval=kind)
        self.notify("info", "Auto-approved: %s %s" % (unit, kind), "; ".join(evidence)[:300])
        return name

    def after_design_check(self, task, res):
        unit = task["unit"]
        if res.get("status") == "approve":
            self.write_auto_approval(unit, "design", ["Research check: %s" % (res.get("summary") or "no deviation"),
                                                     "report docs/reviews/%s_design_check.md" % unit])
        else:
            items = res.get("items") or []
            self.notify("urgent", "Design needs you: %s" % unit,
                        "The design check found points for your decision: %s. Read docs/reviews/%s_design_check.md, "
                        "then answer in the Research chat." % ("; ".join(items)[:300] or res.get("summary", ""), unit))

    def try_auto_release(self, data, gate):
        unit = gate["unit"]
        if not self.cfg.getbool("auto", "release", False):
            return False
        why = []
        if unit in self.cfg.getlist("auto", "manual_release_units"):
            why.append("this group is on the always-check list")
        others = [t for t in data["tasks"] if t["unit"] == unit and t["id"] not in (gate["id"], unit + ".merge", unit + ".end")]
        notdone = [t["id"] for t in others if t["status"] != "done"]
        if notdone:
            why.append("not done: %s" % ", ".join(notdone[:5]))
        open_skyui = [t["id"] for t in data["tasks"] if t["kind"] == "skyui" and t["status"] != "done"]
        if open_skyui:
            why.append("SkyUI work open: %s" % ", ".join(open_skyui))
        u = unit_by_id(self.seq, unit) or {"phases": []}
        qa_dir = self.project.p("qa", "out")
        zips = []
        for ph in u["phases"]:
            ok = sorted(glob.glob(os.path.join(qa_dir, "%s_*_OK.zip" % ph)))
            bad = sorted(glob.glob(os.path.join(qa_dir, "%s_*_ISSUES.zip" % ph)))
            if not ok or (bad and os.path.getmtime(bad[-1]) >= os.path.getmtime(ok[-1])):
                why.append("%s has no clean QA zip" % ph)
            else:
                zips.append(os.path.basename(ok[-1]))
        if why:
            self.notify("urgent", "Release needs you: %s" % unit, "Not released automatically: %s. Look with: ems review %s" % ("; ".join(why), unit),
                        once_key="relman:" + unit)
            return False
        rounds = len([t for t in data["tasks"] if t["unit"] == unit and t["kind"] == "review"])
        self.write_auto_approval(unit, "release", ["independent review clean (docs/reviews/%s.md, %d round(s))" % (unit, rounds),
                                                   "all tasks verified by the runner", "QA: " + ", ".join(zips)])
        return True

    def maybe_digest(self):
        hhmm = self.cfg.get("notify", "digest_time", "08:00")
        if not hhmm:
            return
        import datetime as _dt
        t = _dt.datetime.now()
        h, m = [int(x) for x in hhmm.split(":")]
        if (t.hour, t.minute) < (h, m):
            return
        st = self.state()
        today = t.strftime("%Y-%m-%d")
        if st.get("last_digest") == today:
            return
        since = st.get("last_digest_ts") or (now() - 86400)
        done, autos, blocked = 0, [], []
        path = os.path.join(self.cfg.var_dir, "logs", "events.jsonl")
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("ts", 0) < since:
                    continue
                if e.get("kind") == "task_done":
                    done += 1
                if e.get("kind") == "auto_approval":
                    autos.append("%s %s" % (e.get("unit"), e.get("approval")))
        for tk in self.tasks()["tasks"]:
            if tk["status"] == "blocked":
                blocked.append("%s (%s)" % (tk["id"], (tk.get("blocked") or {}).get("reason")))
        self.update_state(last_digest=today, last_digest_ts=now())
        body = "Tasks done: %d. Auto-approved: %s. Waiting on you: %s. Mode: %s." % (
            done, ", ".join(autos) or "none", ", ".join(blocked) or "nothing", st["mode"])
        self.notify("info", "Daily summary", body)

    def verify(self, task, attempt):
        """The runner checks results itself; it never takes the agent's word for 'done'."""
        root = self.project.root
        kind = task["kind"]
        start = attempt["start"]
        need = []
        if kind == "group_design":
            need.append(os.path.join(root, "docs", "design", "%s.md" % task["unit"]))
        if kind == "review":
            need.append(os.path.join(root, "docs", "reviews", "%s.md" % task["unit"]))
        if kind == "design_check":
            need.append(os.path.join(root, "docs", "reviews", "%s_design_check.md" % task["unit"]))
        if kind == "handover":
            need.append(os.path.join(root, "docs", "HANDOVER.md"))
        if kind == "phase_plan":
            need.append(os.path.join(self.cfg.var_dir, "plans", "%s.json" % task["phase"]))
        for p in need:
            if not os.path.exists(p) or os.path.getmtime(p) < start - 5:
                return "expected output not written in this run: %s" % os.path.relpath(p, root)
        if kind == "phase_plan":
            try:
                self.load_phase_plan(task["phase"])
            except ValueError as e:
                return "phase plan invalid: %s" % e
        allowed = self.cfg.getlist("verify", "allowed_prefixes")
        for cmd in task.get("verify", []):
            if not any(cmd.startswith(a) for a in allowed):
                return "verify command not on the allowed list: %s" % cmd
            self.log("verify %s: %s" % (task["id"], cmd))
            try:
                p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True,
                                   timeout=self.cfg.getint("verify", "timeout_minutes", 40) * 60)
            except subprocess.TimeoutExpired:
                return "verify timed out: %s" % cmd
            with open(attempt["log"], "a") as f:
                f.write("\n[verify] %s -> %d\n%s\n%s\n" % (cmd, p.returncode, p.stdout[-4000:], p.stderr[-4000:]))
            if p.returncode != 0:
                tail = (p.stdout + p.stderr).strip().splitlines()[-15:]
                return "verify failed: %s\n%s" % (cmd, "\n".join(tail))
        return None

    def load_phase_plan(self, phase):
        path = os.path.join(self.cfg.var_dir, "plans", "%s.json" % phase)
        with open(path) as f:
            plan = json.load(f)
        items = plan.get("tasks")
        if not isinstance(items, list) or not items:
            raise ValueError("no tasks")
        ids = set()
        for it in items:
            if it.get("kind") not in PLAN_TASK_KINDS:
                raise ValueError("bad kind %r" % it.get("kind"))
            if not it.get("id") or it["id"] in ids or not it.get("title"):
                raise ValueError("each task needs a unique id and a title")
            ids.add(it["id"])
            if not isinstance(it.get("verify", []), list):
                raise ValueError("verify must be a list")
        return items

    def after_done(self, tid, res):
        self.update_state_service_ok()
        with self.store.locked():
            data = self.tasks()
            task = next(t for t in data["tasks"] if t["id"] == tid)
            task["status"] = "done"
            task["finished"] = now()
            task["result"] = {k: res.get(k) for k in ("summary", "files", "tests", "tag") if k in res}
            if task["kind"] == "phase_plan":
                ph = task["phase"]
                items = self.load_phase_plan(ph)
                idx = data["tasks"].index(task)
                new, prev = [], tid
                for it in items:
                    nid = "%s.%s" % (ph, it["id"])
                    new.append(self._task(nid, it["kind"], task["unit"], phase=ph, deps=[prev], title=it["title"],
                                          details=it.get("details", ""), verify=it.get("verify", []),
                                          model=it.get("model")))
                    prev = nid
                new.append(self._task(ph + ".po", "po", task["unit"], phase=ph, deps=[prev], title="Translations %s" % ph))
                new.append(self._task(ph + ".handover", "handover", task["unit"], phase=ph, deps=[ph + ".po"],
                                      title="Handover %s" % ph))
                data["tasks"][idx + 1:idx + 1] = new
                for t in data["tasks"]:
                    if t["id"] == ph + ".end":
                        t["deps"] = [ph + ".handover"]
            if task["kind"] == "skyui":
                ref = msg_id(task.get("request", ""))
                for t in data["tasks"]:
                    b = t.get("blocked") or {}
                    if t["status"] == "blocked" and b.get("reason") == "skyui" and msg_id(str(b.get("waiting_for"))) == ref:
                        t["status"] = "pending"
                        t["blocked"] = None
                        t.setdefault("notes", []).append("SkyUI finished %s: %s" % (task["request"], res.get("summary", "")))
            self.save_tasks(data)
        self.log("done %s: %s" % (tid, res.get("summary", "")), kind="task_done", task=tid)
        self.write_progress()

    def review_changes(self, tid, res):
        rounds = self.cfg.getint("review", "max_rounds", 2)
        with self.store.locked():
            data = self.tasks()
            task = next(t for t in data["tasks"] if t["id"] == tid)
            task["status"] = "done"
            task["result"] = {"summary": res.get("summary"), "items": res.get("items")}
            r = task.get("round", 1)
            unit = task["unit"]
            if r >= rounds:
                task["status"] = "blocked"
                task["blocked"] = {"reason": "failed", "waiting_for": "owner",
                                   "since": now(), "note": "review still has must-fix items after %d rounds" % r}
                self.save_tasks(data)
                self.notify("urgent", "Review not clean: %s" % unit, "Must-fix items remain after %d rounds. See "
                            "docs/reviews/%s.md; `ems unblock %s` after deciding." % (r, unit, tid))
                return
            idx = data["tasks"].index(task)
            fix = self._task("%s.fix%d" % (unit, r), "fix", unit, deps=[tid], items=res.get("items") or [],
                             title="Fix review round %d" % r)
            rev = self._task("%s.review%d" % (unit, r + 1), "review", unit, deps=[fix["id"]], round=r + 1,
                             title="Independent review %s round %d" % (unit, r + 1))
            data["tasks"][idx + 1:idx + 1] = [fix, rev]
            for t in data["tasks"]:
                if t["id"] == unit + ".gate.release":
                    t["deps"] = [rev["id"]]
            self.save_tasks(data)
        self.log("review %s asked for changes; fix round %d queued" % (tid, r))

    def block(self, tid, res):
        reason = res.get("reason") or ("approval" if res.get("status") == "needs_approval" else
                                       "research" if res.get("status") == "refused" else "question")
        waiting = res.get("waiting_for")
        self.mark(tid, status="blocked", blocked={"reason": reason, "waiting_for": waiting, "since": now(),
                                                  "note": res.get("summary", "")})
        self.checkpoint("blocked: %s" % reason, tid)
        self.log("blocked %s: %s (%s)" % (tid, reason, waiting), kind="blocked", task=tid, reason=reason)
        what = {"approval": "Needs your approval", "question": "Waiting for Research", "skyui": "Waiting for SkyUI",
                "owner-action": "Owner action needed", "toolchain": "Toolchain missing",
                "research": "Research must decide", "service": "Service errors"}.get(reason, "Blocked")
        level = "info" if reason == "skyui" else "urgent"
        self.notify(level, "%s: %s" % (what, tid), "%s%s" % (res.get("summary", ""),
                                                             (" — waiting for %s" % waiting) if waiting else ""))
        self.write_progress()

    def fail(self, tid, problem):
        data = self.tasks()
        task = next(t for t in data["tasks"] if t["id"] == tid)
        att = task["attempts"]
        att[-1]["status"] = "failed"
        att[-1]["summary"] = problem[:500]
        att[-1]["signature"] = _sig(problem)
        model = att[-1]["model"]
        same_model = [a for a in att if a["model"] == model and a.get("status") == "failed"]
        same_sig_twice = len(att) >= 2 and att[-2].get("signature") == att[-1]["signature"]
        notes = (task.get("notes") or []) + ["%s: attempt on %s failed: %s" % (iso(), model, problem[:800])]
        opus_max = self.cfg.getint("escalation", "opus_attempts", 1)
        per_model = self.cfg.getint("escalation", "failures_before_escalation", 2)
        base_is_top = (self.cfg.model_for(task["kind"]) == ESCALATE_TO)
        limit_top = per_model if base_is_top else opus_max
        if model == ESCALATE_TO and (len(same_model) >= limit_top or (base_is_top and same_sig_twice)):
            with self.store.locked():
                d = self.tasks()
                t = next(x for x in d["tasks"] if x["id"] == tid)
                t.update(attempts=att, notes=notes, status="blocked",
                         blocked={"reason": "failed", "waiting_for": "owner", "since": now(), "note": problem[:500]})
                self.save_tasks(d)
            self.checkpoint("failed after escalation", tid)
            self.notify("urgent", "Task failed: %s" % tid, "Failed on %s after escalation. Cause recorded in the log. "
                        "Fix or decide, then `ems retry %s`." % (model, tid))
            self.write_progress()
            return
        next_model = model
        if len(same_model) >= per_model or same_sig_twice:
            next_model = ESCALATE_TO
            self.log("escalating %s to %s" % (tid, ESCALATE_TO), kind="escalate", task=tid)
            self.notify("info", "Escalated to %s" % ESCALATE_TO, "%s failed %d time(s) on %s." % (tid, len(same_model), model))
        with self.store.locked():
            d = self.tasks()
            t = next(x for x in d["tasks"] if x["id"] == tid)
            t.update(attempts=att, notes=notes, status="pending")
            if next_model != model:
                t["model"] = next_model
            self.save_tasks(d)
        self.log("failed %s on %s: %s" % (tid, model, problem[:200]), kind="task_failed", task=tid)

    # ------------------------------------------------------------------ checkpoints and session end
    def git_repos(self):
        repos = []
        for rel in self.cfg.getlist("git", "repos"):
            p = self.project.p(rel)
            if os.path.isdir(os.path.join(p, ".git")):
                repos.append(p)
        if not repos:
            apps = self.project.p("apps")
            if os.path.isdir(apps):
                for d in sorted(os.listdir(apps)):
                    if os.path.isdir(os.path.join(apps, d, ".git")):
                        repos.append(os.path.join(apps, d))
            if os.path.isdir(self.project.p("docs", ".git")):
                repos.append(self.project.p("docs"))
        return repos

    def checkpoint(self, why, tid):
        prefixes = self.cfg.getlist("git", "checkpoint_branch_prefixes", ["phase/", "skyui/"])
        name = self.cfg.get("git", "author_name", "Monirul Hasan")
        email = self.cfg.get("git", "author_email", "89monirulhasan@gmail.com")
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name,
                   GIT_COMMITTER_EMAIL=email)
        done = []
        for repo in self.git_repos():
            try:
                br = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True,
                                    text=True, timeout=30).stdout.strip()
                dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
                                       timeout=60).stdout.strip()
                if not dirty:
                    continue
                if not any(br.startswith(p) for p in prefixes):
                    self.log("checkpoint skipped in %s: branch %s is not a work branch (changes left as they are)"
                             % (repo, br))
                    continue
                subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, timeout=120)
                subprocess.run(["git", "commit", "-q", "-m", "%s: checkpoint (%s)" % (tid.split(".")[0], why)],
                               cwd=repo, env=env, check=True, timeout=120)
                done.append("%s@%s" % (os.path.basename(repo), br))
            except Exception as e:  # noqa: BLE001
                self.log("checkpoint failed in %s: %s" % (repo, e), kind="error")
        cp = {"at": now(), "task": tid, "why": why, "commits": done}
        self.update_state(last_checkpoint=cp)
        self.log("checkpoint (%s): %s" % (why, ", ".join(done) or "state saved, nothing to commit"), kind="checkpoint")
        self.write_progress()

    def end_session(self, why):
        self.update_state(mode="asleep", session=None, wait=None, interrupt=None,
                          next_action="asleep — start a new session with `ems start`")
        self.log("session ended: %s" % why, kind="session_end")
        self.notify("info", "Session ended", "%s. The runner is asleep until you start it again." % why)
        self.write_progress()

    def net_ok(self):
        host = self.cfg.get("limits", "probe_host", "api.anthropic.com")
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ reporting
    def write_progress(self):
        data = self.tasks()
        st = self.state()
        counts = {}
        for t in data["tasks"]:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        lines = ["# EMS runner progress", "", "Updated: %s" % iso(), "",
                 "- Mode: %s%s" % (st["mode"], (" (%s)" % st.get("pause_reason")) if st.get("pause_reason") else ""),
                 "- Unit: %s; done units: %s" % (data.get("unit"), ", ".join(data.get("units_done", [])) or "none"),
                 "- Tasks: %s" % ", ".join("%s %d" % kv for kv in sorted(counts.items())),
                 "- Last checkpoint: %s" % ((st.get("last_checkpoint") or {}).get("why") or "none"), "",
                 "| Task | Kind | Status | Model | Note |", "|---|---|---|---|---|"]
        for t in data["tasks"]:
            b = t.get("blocked") or {}
            model = t.get("model") or (self.cfg.model_for(t["kind"]) if t["kind"] in MODEL_KINDS else "-")
            lines.append("| %s | %s | %s | %s | %s |" % (t["id"], t["kind"], t["status"], model,
                                                         (b.get("reason") or "") + (" " + str(b.get("waiting_for")) if b.get("waiting_for") else "")))
        with open(self.store.path("PROGRESS.md"), "w") as f:
            f.write("\n".join(lines) + "\n")


def main():
    eng = Engine()
    sys.exit(eng.daemon() or 0)


if __name__ == "__main__":
    main()
