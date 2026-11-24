#!/usr/bin/env python3
# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Sandbox tests for the EMS runner using a fake agent CLI and a throwaway practice project.

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER_HOME = os.path.dirname(HERE)
sys.path.insert(0, RUNNER_HOME)

from runner import agent  # noqa: E402
from runner.common import Config, now  # noqa: E402
from runner.engine import Engine  # noqa: E402

OWNER = "Approved by: Monirul Hasan"


def msg(frm, to, about, kind=None, needs="yes", blocking="no", body="text"):
    lines = ["# message", "From: %s      To: %s      Date: 2026-11-26" % (frm, to),
             "About: %s" % about, "Needs action: %s            Blocking: %s" % (needs, blocking)]
    if kind:
        lines.append("Kind: %s" % kind)
    return "\n".join(lines + ["", body]) + "\n"


class Sandbox(object):
    def __init__(self, script, units=None):
        self.dir = tempfile.mkdtemp(prefix="ems_sandbox_")
        self.root = os.path.join(self.dir, "project")
        self.var = os.path.join(self.dir, "var")
        c = os.path.join(self.root, "docs", "collab")
        for d in ("to-dev", "to-research", "to-skyui", "phases", "approvals", "prompts"):
            os.makedirs(os.path.join(c, d))
        os.makedirs(os.path.join(self.root, "docs", "research"))
        os.makedirs(os.path.join(self.root, "apps", "skyui"))
        open(os.path.join(self.root, "docs", "research", "EMS_Phase_List_Master.md"), "w").write("master v1\n")
        open(os.path.join(c, ".last_read_dev"), "w").write("\n")
        open(os.path.join(c, "to-dev", "2026-11-25_030_D30-old.md"), "w").write(msg("Research", "Dev", "D30"))
        self.units = units or [{"id": "GX1", "phases": ["P901", "P902"]}, {"id": "GX2", "phases": ["P903"]}]
        for u in self.units:
            u.setdefault("gates", {"design": "%s_design.md" % u["id"], "release": "%s_release.md" % u["id"]})
        for ph in ("P901", "P902"):
            open(os.path.join(c, "phases", ph + ".md"), "w").write("# %s card\n" % ph)
        seq = os.path.join(self.dir, "sequence.json")
        json.dump({"units": self.units}, open(seq, "w"))
        self.script = os.path.join(self.dir, "script.json")
        json.dump(script, open(self.script, "w"))
        cli = os.path.join(self.dir, "fakecli")
        with open(cli, "w") as f:
            f.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, os.path.join(HERE, "fake_cli.py")))
        os.chmod(cli, 0o755)
        local = os.path.join(self.dir, "local.ini")
        open(local, "w").write("[notify]\ndry_run = yes\n[daemon]\nkeep_awake = no\npoll_seconds = 1\n"
                               "[session]\ngrace_minutes = 0\n[limits]\nprobe_host = 127.0.0.1\n")
        os.environ.update({
            "EMS_PROJECT_ROOT": self.root, "EMS_RUNNER_VAR": self.var, "EMS_SEQUENCE_FILE": seq,
            "EMS_RUNNER_LOCAL": local, "EMS_SECRETS_FILE": os.path.join(self.dir, "nosecrets"),
            "EMS_AGENT_CLI": cli, "FAKE_SCRIPT": self.script,
        })
        self.eng = Engine(Config())
        self.eng.init()
        self.eng.start_session(seconds=3600)

    def c(self, *p):
        return os.path.join(self.root, "docs", "collab", *p)

    def approve(self, name, owner=True):
        open(self.c("approvals", name), "w").write("Group — approval\n%s\n" % (OWNER if owner else "someone"))

    def run(self, n=200):
        for _ in range(n):
            if not self.eng.step():
                return

    def task(self, tid):
        return next((t for t in self.eng.tasks()["tasks"] if t["id"] == tid), None)

    def status(self, tid):
        t = self.task(tid)
        return t and t["status"]

    def calls(self):
        p = os.path.join(self.var, "fake_calls.jsonl")
        return [json.loads(x) for x in open(p)] if os.path.exists(p) else []

    def events(self, kind=None):
        p = os.path.join(self.var, "logs", "events.jsonl")
        ev = [json.loads(x) for x in open(p)]
        return [e for e in ev if kind is None or e.get("kind") == kind]

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class RunnerTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "sb", None):
            self.sb.close()

    def full_unit(self, sb, uid="GX1"):
        sb.run()
        self.assertEqual(sb.status(uid + ".gate.design"), "blocked")
        sb.approve(uid + "_design.md")
        sb.run()
        self.assertEqual(sb.status(uid + ".gate.release"), "blocked")
        sb.approve(uid + "_release.md")
        sb.run()

    def test_01_happy_path_gates_and_order(self):
        self.sb = sb = Sandbox({})
        sb.run()
        self.assertEqual(sb.status("GX1.design"), "done")
        self.assertEqual(sb.status("GX1.gate.design"), "blocked")
        self.assertIsNone(sb.task("P901.t01"), "nothing is built before the design approval")
        notes = [e for e in sb.events("notify") if e["title"] == "Waiting for your approval"]
        self.assertEqual(len(notes), 1, "gate notifies once")
        sb.approve("GX1_design.md")
        sb.run()
        order = [c["task"] for c in sb.calls()]
        self.assertEqual(order, ["GX1.design", "P901.plan", "P901.t01", "P901.t02", "P901.po", "P901.handover",
                                 "P902.plan", "P902.t01", "P902.t02", "P902.po", "P902.handover", "GX1.review"])
        models = {c["task"]: c["model"] for c in sb.calls()}
        self.assertEqual((models["GX1.design"], models["P901.t01"], models["P901.po"], models["GX1.review"]),
                         ("opus", "sonnet", "haiku", "opus"))
        self.assertEqual(sb.status("GX1.gate.release"), "blocked")
        sb.approve("GX1_release.md")
        sb.run()
        self.assertEqual(sb.status("GX1.end"), "done")
        self.assertEqual(sb.eng.tasks()["unit"], "GX2")
        self.assertEqual(sb.status("GX2.cards"), "blocked", "GX2 waits for its card from Research")
        open(sb.c("phases", "P903.md"), "w").write("card")
        sb.run()
        self.assertEqual(sb.status("GX2.design"), "done")

    def test_02_approval_without_owner_name_is_ignored(self):
        self.sb = sb = Sandbox({})
        sb.run()
        sb.approve("GX1_design.md", owner=False)
        sb.run()
        self.assertEqual(sb.status("GX1.gate.design"), "blocked")

    def test_03_session_limit_sleeps_until_reset_then_resumes(self):
        self.sb = sb = Sandbox({r"P901\.t01": ["limit:You've hit your session limit · resets 3:45pm", "done"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        st = sb.eng.state()
        self.assertEqual(st["mode"], "waiting")
        self.assertEqual(st["wait"]["reason"], "limit")
        self.assertGreater(st["wait"]["until"], now())
        self.assertEqual(sb.status("P901.t01"), "pending")
        before = len(sb.calls())
        sb.run()
        self.assertEqual(len(sb.calls()), before, "no requests while limited")
        st["wait"]["until"] = now() - 1
        sb.eng.update_state(wait=st["wait"])
        sb.run()
        self.assertEqual(sb.status("P901.t01"), "done")

    def test_04_limit_past_session_end_stays_asleep(self):
        self.sb = sb = Sandbox({r"P901\.t01": ["limit:You've hit your weekly limit · resets Mon 12:00am"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        st = sb.eng.state()
        st["session"]["end"] = now() - 1
        sb.eng.update_state(session=st["session"])
        sb.run()
        self.assertEqual(sb.eng.state()["mode"], "asleep")

    def test_05_opus_limit_blocks_only_opus(self):
        self.sb = sb = Sandbox({r"P901\.plan": ["limit:You've hit your Opus limit · resets 11:59pm", "done"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        st = sb.eng.state()
        self.assertIn("opus", st["model_blocks"])
        self.assertEqual(st["mode"], "waiting", "only opus work is eligible, so it waits for the opus reset")

    def test_06_auth_pauses_and_notifies(self):
        self.sb = sb = Sandbox({r"GX1\.design": ["auth"]})
        sb.run()
        st = sb.eng.state()
        self.assertEqual(st["mode"], "paused")
        self.assertIn("auth", st["pause_reason"])
        self.assertTrue(any(e["title"] == "Login needed" for e in sb.events("notify")))

    def test_07_network_waits(self):
        self.sb = sb = Sandbox({r"GX1\.design": ["network", "done"]})
        sb.run()
        st = sb.eng.state()
        self.assertEqual((st["mode"], st["wait"]["reason"]), ("waiting", "network"))
        self.assertEqual(sb.status("GX1.design"), "pending")

    def test_08_service_backoff_then_block(self):
        self.sb = sb = Sandbox({r"GX1\.design": ["service"]})
        for i in range(3):
            sb.run()
            st = sb.eng.state()
            self.assertEqual(st["wait"]["reason"], "service")
            st["wait"]["until"] = now() - 1
            sb.eng.update_state(wait=st["wait"])
        sb.run()
        self.assertEqual(sb.status("GX1.design"), "blocked")
        self.assertEqual(sb.task("GX1.design")["blocked"]["reason"], "service")

    def test_09_failures_escalate_then_block(self):
        self.sb = sb = Sandbox({r"P901\.t01": ["fail:test x broke", "fail:test x broke", "fail:still broken"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        runs = [c["model"] for c in sb.calls() if c["task"] == "P901.t01"]
        self.assertEqual(runs, ["sonnet", "sonnet", "opus"], "two failures escalate; opus gets one try")
        self.assertEqual(sb.status("P901.t01"), "blocked")
        self.assertEqual(sb.task("P901.t01")["blocked"]["reason"], "failed")
        self.assertIsNone(sb.task("P901.t02") and sb.status("P901.t02") == "done" or None)

    def test_10_verify_failure_and_missing_result(self):
        plan = {"phase": "P901", "tasks": [{"id": "t01", "kind": "build", "title": "b", "verify": ["test -f nope.txt"]}]}
        self.sb = sb = Sandbox({"__plan__:P901": plan, r"P902\.plan": ["noresult", "done"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        t = sb.task("P901.t01")
        self.assertIn("verify failed", t["attempts"][0]["summary"])
        self.assertNotEqual(t["status"], "done")

    def test_11_bad_verify_command_rejected(self):
        plan = {"phase": "P901", "tasks": [{"id": "t01", "kind": "build", "title": "b", "verify": ["rm -rf /"]}]}
        self.sb = sb = Sandbox({"__plan__:P901": plan})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        self.assertIn("not on the allowed list", sb.task("P901.t01")["attempts"][0]["summary"])

    def test_12_recover_interrupted_task(self):
        self.sb = sb = Sandbox({})
        sb.eng.ensure_unit()
        sb.eng.mark("GX1.design", status="running")
        sb.eng.update_state(current={"task": "GX1.design", "model": "opus", "started": now(), "pid": 999999})
        sb.eng.recover()
        t = sb.task("GX1.design")
        self.assertEqual(t["status"], "pending")
        self.assertIn("do not assume", t["notes"][-1])
        self.assertIsNone(sb.eng.state()["current"])

    def test_13_session_end_interrupts_running_task(self):
        self.sb = sb = Sandbox({r"GX1\.design": ["sleep:30", "done"]})
        st = sb.eng.state()
        st["session"]["end"] = now() + 3
        sb.eng.update_state(session=st["session"])
        t0 = time.time()
        sb.run()
        self.assertLess(time.time() - t0, 25, "run was stopped at session end + grace")
        self.assertEqual(sb.eng.state()["mode"], "asleep")
        self.assertEqual(sb.status("GX1.design"), "pending")
        self.assertIsNotNone(sb.eng.state()["last_checkpoint"])

    def test_14_owner_stop_interrupts(self):
        self.sb = sb = Sandbox({r"GX1\.design": ["sleep:30"]})
        threading.Timer(2.0, lambda: sb.eng.update_state(interrupt="stop")).start()
        sb.run()
        self.assertEqual(sb.eng.state()["mode"], "asleep")

    def test_15_review_changes_add_fix_round(self):
        self.sb = sb = Sandbox({r"GX1\.review": ["changes"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        self.assertEqual(sb.status("GX1.fix1"), "done")
        self.assertEqual(sb.status("GX1.review2"), "done")
        self.assertEqual(sb.task("GX1.gate.release")["deps"], ["GX1.review2"])
        self.assertEqual(sb.status("GX1.gate.release"), "blocked")

    def test_16_message_labels(self):
        self.sb = sb = Sandbox({})
        sb.run()
        open(sb.c("to-dev", "2026-11-26_031_D31-fix.md"), "w").write(msg("Research", "Dev", "D31", "correction"))
        open(sb.c("to-dev", "2026-11-26_032_D32-idea.md"), "w").write(msg("Research", "Dev", "D32", "new-requirement"))
        open(sb.c("to-dev", "2026-11-26_033_D33-x.md"), "w").write(msg("Research", "Dev", "D33"))
        open(sb.c("to-dev", "2026-11-26_034_D34-exc.md"), "w").write(msg("Research", "Dev", "D34", "exception"))
        sb.run()
        backlog = os.listdir(os.path.join(sb.root, "docs", "backlog"))
        self.assertEqual(sorted(backlog), ["2026-11-26_032_D32-idea.md", "2026-11-26_033_D33-x.md"])
        inbox = [t for t in sb.eng.tasks()["tasks"] if t["kind"] == "inbox"]
        self.assertEqual(inbox[0]["messages"], ["2026-11-26_031_D31-fix.md"])
        self.assertEqual(inbox[0]["status"], "done")
        self.assertEqual(sb.eng.tasks()["exceptions"], ["2026-11-26_034_D34-exc.md"])
        self.assertTrue(os.path.exists(sb.c("to-dev", "2026-11-26_032_D32-idea.md")), "originals are never moved")
        self.assertNotIn("2026-11-25_030_D30-old.md", backlog, "pre-runner messages are not re-processed")

    def test_17_skyui_refusal_approval_and_unblock(self):
        req = "2026-11-26_010_R-button.md"
        req2 = "2026-11-26_011_R-tokens.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:skyui:%s" % req, "done"],
                                r"P902\.t01": ["blocked:skyui:%s" % req2, "done"],
                                r"skyui-2026-11-26_010": ["needs_approval:SKYUI_2026-11-26_010.md", "done"],
                                r"skyui-2026-11-26_011": ["refused"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        self.assertEqual(sb.status("P901.t01"), "blocked")
        open(sb.c("to-skyui", req), "w").write(msg("Dev", "SkyUI", "R20"))
        sb.run()
        self.assertEqual(sb.status("skyui-2026-11-26_010"), "blocked")
        self.assertEqual(sb.task("skyui-2026-11-26_010")["blocked"]["reason"], "approval")
        sb.approve("SKYUI_2026-11-26_010.md")
        sb.run()
        self.assertEqual(sb.status("skyui-2026-11-26_010"), "done")
        self.assertEqual(sb.status("P901.t01"), "done", "dev task resumes after SkyUI finished")
        open(sb.c("to-skyui", req2), "w").write(msg("Dev", "SkyUI", "R21"))
        sb.run()
        self.assertEqual(sb.status("skyui-2026-11-26_011"), "blocked")
        self.assertEqual(sb.task("skyui-2026-11-26_011")["blocked"]["reason"], "research")
        self.assertEqual(sb.status("P902.t01"), "blocked")

    def test_18_question_unblocked_by_answer(self):
        q = "2026-11-26_015_Q-unclear.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:question:%s" % q, "done"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        self.assertEqual(sb.status("P901.t01"), "blocked")
        open(sb.c("to-dev", "2026-11-26_035_D35-answer.md"), "w").write(
            msg("Research", "Dev", "D35", "answer", body="Answer to %s: do X." % q))
        sb.run()
        self.assertEqual(sb.status("P901.t01"), "done")

    def test_19_plan_change_without_correction_holds_next_unit(self):
        self.sb = sb = Sandbox({})
        sb.run()
        open(os.path.join(sb.root, "docs", "research", "EMS_Phase_List_Master.md"), "a").write("changed\n")
        self.full_unit(sb)
        self.assertEqual(sb.eng.tasks()["unit"], "GX1", "GX2 does not start")
        self.assertTrue(any(e["title"] == "Plan file changed" for e in sb.events("notify")))

    def test_20_reset_time_parsing(self):
        import datetime as dt
        ref = dt.datetime(2026, 11, 25, 14, 0).timestamp()
        f = lambda s: dt.datetime.fromtimestamp(agent.parse_reset(s, ref))  # noqa: E731
        self.assertEqual(f("3:45pm").strftime("%d %H:%M"), "25 15:45")
        self.assertEqual(f("1pm").strftime("%d %H:%M"), "26 13:00")
        self.assertEqual(f("Mon 12:00am").strftime("%a %H:%M"), "Mon 00:00")
        self.assertEqual(f("Dec 3, 5pm (Asia/Dhaka)").strftime("%m-%d %H:%M"), "12-03 17:00")

    def test_21_classification(self):
        def k(text, retry=None):
            o = agent.Outcome()
            o.retry_errors = retry or []
            agent.classify(o, text)
            return o.kind
        self.assertEqual(k("You've hit your session limit · resets 3:45pm"), "limit")
        self.assertEqual(k("Login expired · Please run /login"), "auth")
        self.assertEqual(k("API Error: Unable to connect to API (ECONNREFUSED)"), "network")
        self.assertEqual(k("API Error: Request rejected (429) · this may be a temporary capacity issue."), "service")
        self.assertEqual(k("something else", ["overloaded"]), "service")
        self.assertEqual(k("Traceback: boom"), "crash")

    def test_22_guard(self):
        guard = os.path.join(RUNNER_HOME, "guard", "pretool_guard.py")
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "docs", "collab", "to-dev"))
        open(os.path.join(root, "docs", "collab", "to-dev", "old.md"), "w").write("x")

        def g(tool, inp, role="dev", task="P001.t01"):
            env = dict(os.environ, EMS_PROJECT_ROOT=root, EMS_ROLE=role, EMS_TASK_ID=task)
            p = subprocess.run([sys.executable, guard], input=json.dumps({"tool_name": tool, "tool_input": inp, "cwd": root}),
                               capture_output=True, text=True, env=env)
            return p.returncode
        B = lambda c, **kw: g("Bash", {"command": c}, **kw)  # noqa: E731
        self.assertEqual(B("rm -rf apps/skysoft-ems"), 2)
        self.assertEqual(B("rm -rf qa/out/P001_x"), 0)
        self.assertEqual(B("docker volume rm erpdash16_db"), 2)
        self.assertEqual(B("bench --site ems.localhost drop-site"), 2)
        self.assertEqual(B("git push origin main"), 2)
        self.assertEqual(B("git reset --hard HEAD~1"), 2)
        self.assertEqual(B("echo ok > docs/collab/approvals/G01_design.md"), 2)
        self.assertEqual(B("ls docs/collab/approvals"), 0)
        self.assertEqual(B("cat ~/.skysoft/secrets.env"), 2)
        self.assertEqual(B("git status && npm test"), 0)
        self.assertEqual(g("Write", {"file_path": "docs/collab/approvals/G01_release.md"}), 2)
        self.assertEqual(g("Edit", {"file_path": "docs/research/EMS_Development_Guide.md"}), 2)
        self.assertEqual(g("Edit", {"file_path": "docs/collab/to-dev/old.md"}), 2)
        self.assertEqual(g("Write", {"file_path": "docs/collab/to-research/new.md"}), 0)
        self.assertEqual(g("Edit", {"file_path": "apps/skyui/skyui/x.css"}), 2)
        self.assertEqual(g("Edit", {"file_path": "apps/skyui/skyui/x.css"}, role="skyui"), 0)
        self.assertEqual(g("Edit", {"file_path": "apps/skysoft-ems/x.py"}, role="skyui"), 2)
        self.assertEqual(g("Read", {"file_path": "source_snapshot/education/x.py"}), 2)
        os.makedirs(os.path.join(root, "docs", "collab", "approvals"))
        open(os.path.join(root, "docs", "collab", "approvals", "G01_release.md"), "w").write("x")
        self.assertEqual(B("git push origin main --tags", task="G01.merge"), 0)
        self.assertEqual(B("git push origin main", task="G02.merge"), 2)
        shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1, argv=[sys.argv[0]])
