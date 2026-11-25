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
import unittest.mock

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
    def __init__(self, script, units=None, auto=False, research=False):
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
                               "[session]\ngrace_minutes = 0\n[limits]\nprobe_host = 127.0.0.1\n"
                               "[auto]\ndesign = %s\nrelease = %s\nresearch = %s\nmanual_release_units = GX2\n"
                               "[notify]\ndigest_time =\n" % ((("yes", "yes") if auto else ("no", "no")) +
                                                               ("yes" if research else "no",)))
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

    def test_25_auto_research_answers_and_dev_resumes(self):
        q = "2026-11-26_015_Q-unclear.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:question:%s" % q, "done"],
                                r"research-2026-11-26_015": ["done"]}, research=True)
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        self.assertEqual(sb.status("P901.t01"), "blocked")
        open(sb.c("to-research", q), "w").write(msg("Dev", "Research", "P901.t01", blocking="yes"))
        sb.run()
        self.assertEqual(sb.status("research-2026-11-26_015"), "done")
        self.assertEqual([c["model"] for c in sb.calls() if c["task"].startswith("research-")], ["opus"])
        self.assertEqual(sb.status("P901.t01"), "done", "Development resumes after the automatic answer")

    def test_26_auto_research_parks_owner_items(self):
        q = "2026-11-26_016_Q-scope.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:question:%s" % q, "done"],
                                r"research-2026-11-26_016": ["blocked:owner:2026-11-26_OQ_research-2026-11-26_016.md"]},
                               research=True)
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        open(sb.c("to-research", q), "w").write(msg("Dev", "Research", "P901.t01", blocking="yes"))
        open(sb.c("to-research", "2026-11-26_017_A-fyi.md"), "w").write(msg("SkyUI", "Research", "R1", needs="no"))
        sb.run()
        self.assertIsNone(sb.task("research-2026-11-26_017"), "messages that need no action start no run")
        self.assertEqual(sb.task("research-2026-11-26_016")["blocked"]["reason"], "owner")
        self.assertEqual(sb.status("P901.t01"), "blocked")
        open(sb.c("to-dev", "2026-11-26_040_D40-owner-answer.md"), "w").write(
            msg("Research", "Dev", "D40", "answer", body="Answer to %s (owner chose option 1)." % q))
        sb.run()
        self.assertEqual(sb.status("research-2026-11-26_016"), "done")
        self.assertEqual(sb.status("P901.t01"), "done")

    def test_28_research_catch_up_for_waiting_question(self):
        q = "2026-11-26_019_Q-old.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:question:%s" % q, "done"],
                                r"research-2026-11-26_019": ["done"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        open(sb.c("to-research", q), "w").write(msg("Dev", "Research", "P901.t01", blocking="yes"))
        sb.run()
        self.assertIsNone(sb.task("research-2026-11-26_019"), "research off: nothing queued")
        with open(os.environ["EMS_RUNNER_LOCAL"], "a") as f:
            f.write("[auto]\nresearch = yes\n")
        sb.eng.cfg = Config()
        sb.run()
        self.assertEqual(sb.status("research-2026-11-26_019"), "done")
        self.assertEqual(sb.status("P901.t01"), "done")

    def test_29_owner_action_goes_to_research_first(self):
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:owner-action:", "done"], r"research-": ["done"]}, research=True)
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        qs = [n for n in os.listdir(sb.c("to-research")) if "owner-action-check" in n]
        self.assertEqual(len(qs), 1, "the runner writes one Q for the owner-action stop")
        text = open(sb.c("to-research", qs[0])).read()
        self.assertIn("From: Runner", text)
        self.assertIn("need help", text)
        self.assertEqual(sb.status("P901.t01"), "done", "Research answered; the task resumed without the owner")
        self.assertFalse([e for e in sb.events("notify") if e.get("level") == "urgent" and "P901.t01" in e.get("title", "")],
                         "no urgent owner alert for a stop Research could answer")

    def test_30_owner_action_reaches_owner_after_two_checks_or_research_off(self):
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:owner-action:"] * 3, r"research-": ["done", "done"]},
                               research=True)
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        t = sb.task("P901.t01")
        self.assertEqual(t["blocked"]["reason"], "owner-action", "third stop goes to the owner")
        self.assertEqual(len([n for n in os.listdir(sb.c("to-research")) if "owner-action-check" in n]), 2)
        sb.close()
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:owner-action:"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        self.assertEqual(sb.task("P901.t01")["blocked"]["reason"], "owner-action", "research off: owner as before")
        self.assertFalse([n for n in os.listdir(sb.c("to-research")) if "owner-action-check" in n])

    def test_31_owner_action_naming_its_own_q_waits_for_the_answer(self):
        q = "2026-11-26_021_Q-login.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:owner-action:%s" % q, "done"],
                                r"research-2026-11-26_021": ["done"]}, research=True)
        sb.run()
        open(sb.c("to-research", q), "w").write(msg("Dev", "Research", "P901.t01", blocking="yes", needs="no"))
        sb.approve("GX1_design.md")
        sb.run()
        self.assertFalse([n for n in os.listdir(sb.c("to-research")) if "owner-action-check" in n],
                         "no extra runner Q when the agent already asked")
        self.assertEqual(sb.status("research-2026-11-26_021"), "done", "catch-up answers the agent's own Q")
        self.assertEqual(sb.status("P901.t01"), "done", "Research's answer resumes the task")
        self.assertIn("2026-11-26_921", " ".join(sb.task("P901.t01").get("notes", [])))

    def test_32_header_quoted_in_body_does_not_override_kind(self):
        q = "2026-11-26_022_Q-x.md"
        self.sb = sb = Sandbox({r"P901\.t01": ["blocked:question:%s" % q, "done"]})
        sb.run()
        sb.approve("GX1_design.md")
        sb.run()
        body = "Answer to %s.\nNote: D40 had `Kind: answer`, `About: P901.t01`, sent earlier." % q
        open(sb.c("to-dev", "2026-11-26_042_D42-answer.md"), "w").write(msg("Research", "Dev", "D42", "answer", body=body))
        sb.run()
        self.assertEqual(sb.status("P901.t01"), "done")
        self.assertFalse(os.path.exists(os.path.join(sb.root, "docs", "backlog", "2026-11-26_042_D42-answer.md")))

    def test_27_research_off_only_notifies(self):
        q = "2026-11-26_018_Q-x.md"
        self.sb = sb = Sandbox({})
        sb.run()
        open(sb.c("to-research", q), "w").write(msg("Dev", "Research", "P901.t01", blocking="yes"))
        sb.run()
        self.assertIsNone(sb.task("research-2026-11-26_018"))

    def test_19_plan_change_without_correction_holds_next_unit(self):
        self.sb = sb = Sandbox({})
        sb.run()
        open(os.path.join(sb.root, "docs", "research", "EMS_Phase_List_Master.md"), "a").write("changed\n")
        self.full_unit(sb)
        self.assertEqual(sb.eng.tasks()["unit"], "GX1", "GX2 does not start")
        self.assertTrue(any(e["title"] == "Plan file changed" for e in sb.events("notify")))

    def test_23_auto_design_and_release(self):
        self.sb = sb = Sandbox({r"GX1\.design_check": ["approve"]}, auto=True)
        sb.run()
        self.assertTrue(os.path.exists(sb.c("approvals", "GX1_design.md")), "clean check writes the design approval")
        self.assertIn("automatic under AUTO-Q10", open(sb.c("approvals", "GX1_design.md")).read())
        self.assertEqual(sb.status("GX1.review"), "done", "build ran without stopping")
        self.assertEqual(sb.status("GX1.gate.release"), "blocked", "no clean QA zips yet, so release waits")
        qa = os.path.join(sb.root, "qa", "out")
        os.makedirs(qa)
        for ph in ("P901", "P902"):
            open(os.path.join(qa, "%s_20261125_0100_OK.zip" % ph), "w").write("z")
        sb.run()
        self.assertTrue(os.path.exists(sb.c("approvals", "GX1_release.md")))
        self.assertEqual(sb.status("GX1.end"), "done")
        self.assertEqual(sb.eng.tasks()["unit"], "GX2")

    def test_24_design_check_needs_owner_and_manual_release(self):
        self.sb = sb = Sandbox({r"GX1\.design_check": ["owner"]}, auto=True)
        sb.run()
        self.assertFalse(os.path.exists(sb.c("approvals", "GX1_design.md")))
        self.assertEqual(sb.status("GX1.gate.design"), "blocked")
        self.assertTrue(any(e["title"].startswith("Design needs you") for e in sb.events("notify")))
        sb.approve("GX1_design.md")
        sb.run()
        os.makedirs(os.path.join(sb.root, "qa", "out"))
        for ph in ("P901", "P902"):
            open(os.path.join(sb.root, "qa", "out", "%s_x_OK.zip" % ph), "w").write("z")
        open(os.path.join(sb.root, "qa", "out", "P902_y_ISSUES.zip"), "w").write("z")
        sb.run()
        self.assertFalse(os.path.exists(sb.c("approvals", "GX1_release.md")), "a newer ISSUES zip stops auto release")
        self.assertTrue(any(e["title"] == "Release needs you: GX1" for e in sb.events("notify")))

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
        self.assertEqual(B("git -C apps/skyui log --oneline -1 2>&1; grep -n x docs/research/INDEX.md | head"), 0)
        self.assertEqual(B("ls x 2>&1 | head; grep -n y docs/research/a.md"), 0)
        self.assertEqual(B("echo hi > docs/research/a.md"), 2)
        self.assertEqual(B("cp notes.md docs/research/"), 2)
        self.assertEqual(B("cp docs/research/INDEX.md /tmp/x.md"), 0)
        self.assertEqual(B("git -C apps/skyui commit -am x"), 2)
        self.assertEqual(B("cat > infra/p000/hooks/pre_file.py <<'EOF'\nDENY = ['source_snapshot/education/']\nEOF"), 0)
        self.assertEqual(B("grep -r x source_snapshot/education/"), 2)
        self.assertEqual(B("cd source_snapshot/education && ls"), 2)
        self.assertEqual(B("mysql <<'SQL'\nDROP TABLE tabX;\nSQL"), 2)
        self.assertEqual(B("cat > infra/p000/hooks/pre_bash.py <<'EOF'\nPATS = [r'DROP\\s+TABLE', 'TRUNCATE TABLE']\nEOF"), 0)
        self.assertEqual(B("bench --site ems.localhost mariadb <<'SQL'\nTRUNCATE TABLE tabX;\nSQL"), 2)
        self.assertEqual(B("sed -i s/a/b/ apps/skyui/x.css"), 2)
        self.assertEqual(g("Write", {"file_path": "docs/collab/approvals/G01_release.md"}), 2)
        self.assertEqual(g("Edit", {"file_path": "docs/research/EMS_Development_Guide.md"}), 2)
        self.assertEqual(g("Edit", {"file_path": "docs/collab/to-dev/old.md"}), 2)
        self.assertEqual(g("Write", {"file_path": "docs/collab/to-research/new.md"}), 0)
        self.assertEqual(g("Edit", {"file_path": "apps/skyui/skyui/x.css"}), 2)
        self.assertEqual(g("Edit", {"file_path": "apps/skyui/skyui/x.css"}, role="skyui"), 0)
        self.assertEqual(g("Edit", {"file_path": "apps/skysoft-ems/x.py"}, role="skyui"), 2)
        self.assertEqual(g("Read", {"file_path": "source_snapshot/education/x.py"}), 2)
        R = dict(role="research", task="research-2026-11-26_015")
        self.assertEqual(g("Edit", {"file_path": "docs/research/EMS_Owner_Decisions_Register_Part4.md"}, **R), 0)
        self.assertEqual(g("Write", {"file_path": "docs/collab/to-dev/new_D90.md"}, **R), 0)
        self.assertEqual(g("Write", {"file_path": "docs/collab/owner/2026-11-26_OQ_x.md"}, **R), 0)
        self.assertEqual(g("Edit", {"file_path": "docs/collab/to-dev/old.md"}, **R), 2)
        self.assertEqual(g("Write", {"file_path": "docs/collab/approvals/G01_design.md"}, **R), 2)
        self.assertEqual(g("Edit", {"file_path": "apps/skysoft-ems/x.py"}, **R), 2)
        self.assertEqual(g("Edit", {"file_path": "infra/scripts/backup.sh"}, **R), 2)
        self.assertEqual(g("Bash", {"command": "git -C docs commit -am x"}, **R), 2)
        self.assertEqual(g("Bash", {"command": "echo x > infra/scripts/a.sh"}, **R), 2)
        self.assertEqual(g("Bash", {"command": "echo hi >> docs/research/SYNC_TO_PROJECT.md"}, **R), 0)
        self.assertEqual(g("Bash", {"command": "grep -n x docs/p000_apply_1.txt | head"}, **R), 0)
        os.makedirs(os.path.join(root, "docs", "collab", "approvals"))
        open(os.path.join(root, "docs", "collab", "approvals", "G01_release.md"), "w").write("x")
        self.assertEqual(B("git push origin main --tags", task="G01.merge"), 0)
        self.assertEqual(B("git push origin main", task="G02.merge"), 2)
        shutil.rmtree(root)



class FakeTelegram(object):
    def __init__(self):
        self.sent, self.updates, self.mid = [], [], 100

    def call(self, method, params):
        if method == "sendMessage":
            self.mid += 1
            self.sent.append(dict(params, message_id=self.mid))
            return {"result": {"message_id": self.mid}}
        if method == "getUpdates":
            off = int(params.get("offset", 0))
            return {"result": [u for u in self.updates if u["update_id"] >= off]}
        return {}


A, B, STRANGER = "5014180525", "8926898835", "999"


class ConsentTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "sb", None):
            self.sb.close()

    def gate(self, on=True):
        self.sb = sb = Sandbox({})
        self.tg = FakeTelegram()
        c = sb.eng.consent
        c._client = self.tg
        c.enabled = lambda: on
        c.ids = lambda: [A, B]
        sb.eng.cfg.getint  # noqa: B018 (config object exists)
        sb.run()
        return sb

    def reply(self, uid, sender, text, reply_to=None):
        m = {"from": {"id": int(sender)}, "text": text, "message_id": uid}
        if reply_to:
            m["reply_to_message"] = {"message_id": reply_to}
        self.tg.updates.append({"update_id": uid, "message": m})

    def answers(self, sb):
        return [f for f in os.listdir(sb.c("to-research")) if "Q-consent" in f]

    def test_33_brief_goes_to_both_ids_in_parts(self):
        sb = self.gate()
        self.assertEqual(sb.status("GX1.gate.design"), "blocked")
        chats = {m["chat_id"] for m in self.tg.sent}
        self.assertEqual(chats, {A, B})
        self.assertTrue(all(len(m["text"]) <= 4096 for m in self.tg.sent))
        self.assertIn("CONSENT NEEDED GX1-design", self.tg.sent[0]["text"])
        self.assertIn("reply_markup", self.tg.sent[0])

    def test_34_long_brief_is_split_under_limit(self):
        from runner.consent import split_parts
        parts = split_parts(("line of text\n" * 2000) + "x" * 9000)
        self.assertGreater(len(parts), 3)
        self.assertTrue(all(len(p) < 4096 for p in parts))

    def test_35_valid_reply_writes_one_answer_then_second_is_rejected(self):
        sb = self.gate()
        head = self.tg.sent[0]["message_id"]
        before = sorted(os.listdir(sb.c("to-research")))
        self.reply(1, A, "1", reply_to=head)
        self.reply(2, B, "/approve GX1-design 2")
        sb.eng.consent.poll(force=True)
        new = [f for f in os.listdir(sb.c("to-research")) if f not in before]
        self.assertEqual(len(new), 1)
        text = open(sb.c("to-research", new[0])).read()
        self.assertIn("Kind: answer", text)
        self.assertIn("option 1", text)
        self.assertIn("From: Runner      To: Research", text)
        self.assertEqual(sorted(os.listdir(sb.c("to-research")))[:len(before)], before)

    def test_36_other_senders_option_and_code_are_rejected(self):
        sb = self.gate()
        head = self.tg.sent[0]["message_id"]
        self.reply(1, STRANGER, "1", reply_to=head)
        self.reply(2, A, "/approve GX1-design 9")
        self.reply(3, A, "/approve NOPE-design 1")
        self.reply(4, A, "approve everything")
        sb.eng.consent.poll(force=True)
        self.assertEqual(self.answers(sb), [])

    def test_37_button_tap_counts_and_restart_does_not_reprocess(self):
        sb = self.gate()
        self.tg.updates.append({"update_id": 7, "callback_query": {"from": {"id": int(B)}, "data": "c|GX1-design|2",
                                                                      "message": {"message_id": 1}}})
        sb.eng.consent.poll(force=True)
        self.assertEqual(len(self.answers(sb)), 1)
        self.assertEqual(sb.eng.consent.state()["offset"], 8)
        for f in self.answers(sb):  # even with the file gone, a restarted listener must not answer again
            pass
        from runner.consent import Consent
        c2 = Consent(sb.eng.cfg, sb.eng.store, sb.eng.log, sb.eng.project, client=self.tg)
        c2.enabled = lambda: True
        c2.ids = lambda: [A, B]
        c2.poll(force=True)
        self.assertEqual(len(self.answers(sb)), 1)

    def test_38_task_resumes_when_approval_file_appears(self):
        sb = self.gate()
        self.reply(1, A, "/approve GX1-design 1")
        sb.eng.consent.poll(force=True)
        self.assertEqual(sb.status("GX1.gate.design"), "blocked", "an answer alone does not approve")
        sb.approve("GX1_design.md")
        sb.run()
        self.assertEqual(sb.status("GX1.gate.design"), "done")

    def test_39_switch_off_sends_nothing(self):
        sb = self.gate(on=False)
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(sb.status("GX1.gate.design"), "blocked")
        self.assertEqual(sb.eng.consent.poll(force=True), 0)



class FakeTG(FakeTelegram):
    """Fake Telegram that also records edits, uploads and every call."""

    def __init__(self):
        FakeTelegram.__init__(self)
        self.calls, self.edits, self.uploads, self.fail_media = [], [], [], False

    def call(self, method, params):
        if method == "editMessageText":
            self.calls.append((method, params))
            self.edits.append(params)
            return {"result": {}}
        r = FakeTelegram.call(self, method, params)
        self.calls.append((method, self.sent[-1] if method == "sendMessage" else params))
        return r

    def upload(self, method, params, files):
        if self.fail_media and method == "sendMediaGroup":
            raise OSError("boom")
        self.uploads.append((method, params, files))
        return {"result": True}


def png(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


class ControlTests(unittest.TestCase):
    uid = 0

    def setUp(self):
        self.sb = sb = Sandbox({})
        self.tg = FakeTG()
        self.ctl = sb.eng.control
        self.ctl._client = self.tg
        sb.eng.consent._client = self.tg
        self.ctl.enabled = lambda: True
        sb.eng.consent.ids = lambda: [A, B]

    def tearDown(self):
        self.sb.close()

    # -- helpers
    def push(self, upd):
        ControlTests.uid += 1
        upd["update_id"] = ControlTests.uid
        self.tg.updates.append(upd)
        self.sb.eng.consent.poll(force=True)

    def say(self, who, text, chat="private", date=None):
        m = {"from": {"id": int(who)}, "text": text, "message_id": 5, "chat": {"id": int(who), "type": chat}}
        if date:
            m["date"] = date
        self.push({"message": m})

    def mid(self):
        return [m for m in self.tg.sent if "reply_markup" in m][-1]["message_id"]

    def kb(self, mid):
        for method, p in reversed(self.tg.calls):
            if method in ("sendMessage", "editMessageText") and p.get("message_id") == mid and "reply_markup" in p:
                return [b for row in json.loads(p["reply_markup"])["inline_keyboard"] for b in row]
        return []

    def labels(self, mid):
        return [b["text"] for b in self.kb(mid)]

    def tap_data(self, who, mid, data):
        self.push({"callback_query": {"id": "q", "from": {"id": int(who)}, "data": data,
                                      "message": {"message_id": mid, "chat": {"id": int(who), "type": "private"}}}})

    def tap(self, who, mid, label):
        b = next(b for b in self.kb(mid) if b["text"].startswith(label))
        self.tap_data(who, mid, b["callback_data"])

    def text_of(self, mid):
        for method, p in reversed(self.tg.calls):
            if method in ("sendMessage", "editMessageText") and p.get("message_id") == mid:
                return p["text"]

    def off(self):
        self.sb.eng.update_state(mode="asleep", session=None)

    def recorder(self, name, result=(True, "ok")):
        from unittest import mock
        from runner import actions
        patcher = mock.patch.object(actions, name, side_effect=lambda *a, **k: result)
        rec = patcher.start()
        self.addCleanup(patcher.stop)
        return rec

    # -- tree
    def walk(self, ctl, path="home", seen=None, depth=0):
        seen = seen if seen is not None else {}
        text, rws = ctl.screen(path)
        seen[path] = rws
        for row in rws:
            for label, action, arg in row:
                self.assertLessEqual(len(("m:%s:%s:abcdef" % (action, arg)).encode()), 64)
                if action == "go" and arg not in seen and depth < 8:
                    self.walk(ctl, arg, seen, depth + 1)
        return seen

    def make_runs(self):
        for ph in ("P901", "P902"):
            for step, lang, theme in (("001", "bn", "light"), ("002", "bn", "light"), ("002", "en", "dark"),
                                     ("002", "ar", "light"), ("002", "bn", "dark"), ("002", "en", "light"), ("002", "ar", "dark")):
                png(os.path.join(self.sb.root, "qa", "out", ph + "_20261126_0100", "screenshots",
                                 "%s_S%s_home_open_%s_%s.png" % (ph, step, lang, theme)))

    def test_40_every_level_has_back_and_changes_need_confirm(self):
        self.make_runs()
        for setup in ("running", "off", "paused"):
            if setup == "off":
                self.off()
            elif setup == "paused":
                self.sb.eng.start_session(seconds=3600)
                self.sb.eng.update_state(mode="paused")
            seen = self.walk(self.ctl)
            self.assertGreater(len(seen), 5)
            for path, rws in seen.items():
                labels = [b[0] for row in rws for b in row]
                if path != "home":
                    self.assertTrue(any(l.startswith("⬅") for l in labels), "%s (%s) has no Back" % (path, setup))
                for row in rws:
                    for label, action, arg in row:
                        if action == "run":  # a Run button lives only on a screen that also offers Back
                            self.assertTrue(any(l.startswith("⬅") for l in labels))
                if path.split(".")[-1] in ("stop", "task", "now", "resume") or path in ("onoff.start.m300", "dur.m120"):
                    self.assertIn("✅ Run", labels, path)
            self.assertFalse(any(a == "run" for row in seen["home"] for _, a, _ in row))

    def test_41_status_runs_at_once_and_changes_nothing(self):
        self.say(A, "ems")
        mid = self.mid()
        before = self.sb.eng.state()["mode"]
        self.tap(A, mid, "📊")
        self.assertIn("Mode:", self.text_of(mid))
        self.assertIn("🔄 Refresh", self.labels(mid))
        self.assertEqual(self.sb.eng.state()["mode"], before)
        self.assertIn("EMS · running", self.text_of(mid))

    def test_42_buttons_match_state(self):
        c = self.ctl
        flat = lambda path: [l for row in c.screen(path)[1] for l, _, _ in row]  # noqa: E731
        self.assertIn("⏱ Duration", flat("home"))
        self.assertEqual(flat("pr")[:2], ["Pause after this task", "Pause now"])
        self.assertNotIn("Resume", flat("pr"))
        self.sb.eng.update_state(mode="paused")
        self.assertEqual(flat("pr")[0], "Resume")
        self.assertNotIn("Pause now", flat("pr"))
        self.off()
        self.assertNotIn("⏱ Duration", flat("home"))
        self.assertNotIn("⏯ Pause / Resume", flat("home"))
        self.assertIn("Start session", flat("onoff"))
        self.assertIn("not available", c.screen("dur")[0])
        self.assertIn("EMS · off", c.state_line())

    def test_43_each_changing_action_calls_shared_function_once(self):
        patcher = unittest.mock.patch("runner.control.MAX_CHANGES_PER_MIN", 99)
        patcher.start()
        self.addCleanup(patcher.stop)
        start, stop = self.recorder("start"), self.recorder("stop")
        pause, resume, setd = self.recorder("pause"), self.recorder("resume"), self.recorder("set_duration")

        def flow(who, labels):
            self.say(who, "ems")
            mid = self.mid()
            for l in labels:
                self.tap(who, mid, l)
        flow(A, ["⏯", "Pause now", "✅"])
        pause.assert_called_once_with(self.sb.eng, True)
        pause.reset_mock()
        flow(A, ["⏯", "Pause after", "✅"])
        pause.assert_called_once_with(self.sb.eng, False)
        flow(A, ["⏱", "2 h", "✅"])
        setd.assert_called_once_with(self.sb.eng, "120m", from_now=True)
        flow(A, ["⏻", "Stop", "✅"])
        stop.assert_called_once_with(self.sb.eng)
        self.sb.eng.update_state(mode="paused")
        flow(B, ["⏯", "Resume", "✅"])
        resume.assert_called_once_with(self.sb.eng)
        self.off()
        flow(A, ["⏻", "Start", "5 h", "✅"])
        start.assert_called_once_with(self.sb.eng, duration="300m", until=None)
        start.reset_mock()
        self.off()
        flow(A, ["⏻", "Start", "Until 08:00", "✅"])
        start.assert_called_once_with(self.sb.eng, duration=None, until="08:00")

    def test_44_cli_still_works(self):
        from runner import cli
        import io
        import contextlib
        def run(*args):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(list(args))
            return rc, buf.getvalue()
        rc, out = run("pause")
        self.assertEqual(rc, 0)
        self.assertEqual(self.sb.eng.state()["mode"], "paused")
        self.assertIn("Paused", out)
        rc, out = run("status")
        self.assertIn("Mode:        paused", out)
        with unittest.mock.patch.object(cli, "ensure_daemon"):
            rc, out = run("resume")
        self.assertEqual((rc, self.sb.eng.state()["mode"]), (0, "running"))
        rc, out = run("set-duration", "07:30")
        self.assertIn("Session now ends", out)
        rc, out = run("set-duration", "nonsense")
        self.assertEqual(rc, 1)
        self.off()
        rc, out = run("set-duration", "2h")
        self.assertEqual(rc, 1)

    def test_45_nonce_reuse_and_expiry_refused(self):
        pause = self.recorder("pause")
        self.say(A, "/ems pause")
        mid = self.mid()
        run = next(b for b in self.kb(mid) if b["text"].startswith("✅"))
        self.tap_data(A, mid, run["callback_data"])
        self.tap_data(A, mid, run["callback_data"])  # same nonce again
        self.assertEqual(pause.call_count, 1)
        # a stale menu: older than 10 minutes
        self.say(A, "/ems pause")
        mid2 = self.mid()
        run2 = next(b for b in self.kb(mid2) if b["text"].startswith("✅"))
        real = time.time()
        with unittest.mock.patch("runner.control.now", lambda: real + 601):
            self.tap_data(A, mid2, run2["callback_data"])
        self.assertEqual(pause.call_count, 1)
        # a forged nonce or changed action
        self.say(A, "/ems pause")
        mid3 = self.mid()
        self.tap_data(A, mid3, "m:run:pr.task:ffffff")
        self.tap_data(A, mid3, run2["callback_data"])
        self.assertEqual(pause.call_count, 1)
        log = open(os.path.join(self.sb.var, "telegram_control.log")).read()
        self.assertIn("refused", log)

    def test_46_state_change_redraws_instead_of_running(self):
        pause = self.recorder("pause")
        self.say(A, "/ems pause")
        mid = self.mid()
        self.sb.eng.update_state(mode="paused")  # the other person paused meanwhile
        self.tap(A, mid, "✅")
        pause.assert_not_called()
        self.assertIn("state changed", self.text_of(mid))
        self.assertIn("EMS · paused", self.text_of(mid))

    def test_47_unknown_sender_and_group_chat_ignored(self):
        self.say(STRANGER, "ems")
        self.say(A, "ems", chat="group")
        self.say(A, "ems", chat="supergroup")
        self.assertEqual(self.tg.sent, [])
        self.say(A, "ems")
        mid = self.mid()
        pause = self.recorder("pause")
        self.push({"callback_query": {"id": "q", "from": {"id": int(STRANGER)}, "data": self.kb(mid)[0]["callback_data"],
                                      "message": {"message_id": mid, "chat": {"id": int(A), "type": "private"}}}})
        self.assertEqual(self.tg.edits, [])
        log = open(os.path.join(self.sb.var, "telegram_control.log")).read()
        self.assertIn("sender not on the list", log)
        self.assertIn("not a private chat", log)
        self.assertNotIn("TOKEN", log)

    def test_48_custom_duration(self):
        from runner.control import parse_custom
        for ok, spec in (("2h30m", "m150"), ("90m", "m90"), ("5h", "m300"), ("24h", "m1440"), ("06:30", "u0630"), ("23:59", "u2359"),
                         ("1h99m", "m159"), ("2H30M", "m150")):
            self.assertEqual(parse_custom(ok), spec, ok)
        for bad in ("5", "25h", "0m", "0h0m", "24:00", "6:30", "1000m", "2h 30m", "1.5h", "-1h", "now", "", "2h30", "h", "30m\nrm", "06:60"):
            self.assertIsNone(parse_custom(bad), bad)
        start = self.recorder("start")
        self.off()
        self.say(A, "ems")
        mid = self.mid()
        for l in ("⏻", "Start", "Custom"):
            self.tap(A, mid, l)
        self.assertIn("one reply", self.text_of(mid))
        n = len(self.tg.sent)
        self.say(A, "banana")
        self.assertIn("Not accepted", self.tg.sent[-1]["text"])
        self.say(A, "25h")
        self.assertEqual(len(self.tg.sent), n + 2)
        self.say(A, "2h30m")
        self.assertIn("Start a session until", self.text_of(mid))
        self.tap(A, mid, "✅")
        start.assert_called_once_with(self.sb.eng, duration="150m", until=None)

    def test_49_presets_come_from_config(self):
        cfg = self.sb.eng.cfg
        cfg.cp.set("control", "presets", "30m, 4h, 06:00")
        cfg.cp.set("control", "default_preset", "4h")
        self.off()
        text, rws = self.ctl.screen("onoff.start")
        labels = [l for row in rws for l, _, _ in row]
        self.assertEqual(labels[:4], ["4 h (default)", "30 min", "Until 06:00", "Custom…"])
        cfg.cp.set("control", "presets", "1h, 2h, 3h, 5h, 08:00, 12:00")
        cfg.cp.set("control", "default_preset", "5h")
        labels = [l for row in self.ctl.screen("onoff.start")[1] for l, _, _ in row]
        self.assertEqual(labels[:7], ["5 h (default)", "1 h", "2 h", "3 h", "Until 08:00", "Until 12:00", "Custom…"])

    def test_50_screenshot_albums_limits(self):
        from runner import shots
        d = os.path.join(self.sb.dir, "many")
        items = []
        for i in range(36):
            p = os.path.join(d, "f%02d.png" % i)
            png(p)
            items.append({"path": p})
        big = os.path.join(d, "big.png")
        with open(big, "wb") as f:
            f.truncate(shots.MAX_BYTES + 1)
        albums, skipped, rest = shots.plan_albums([{"path": big}] + items)
        self.assertEqual([len(a) for a in albums], [10, 10, 10])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(rest, 6)
        self.assertEqual(shots.plan_albums(items[:3])[0], [items[:3]])
        self.assertEqual(shots.plan_albums([])[0], [])

    def test_51_screenshots_and_review_are_sent(self):
        self.make_runs()
        open(os.path.join(self.sb.root, "qa", "out", "P901_20261126_0100", "REPORT.md"), "w").write("step 3 FAILED\n")
        self.say(A, "ems")
        mid = self.mid()
        for l in ("🔍", "Screenshots", "GX1", "P901", "Key screens"):
            self.tap(A, mid, l)
        groups = [p for m, p, _ in self.tg.uploads if m == "sendMediaGroup"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(json.loads(groups[0]["media"])), 6)  # one key screen in 6 variants
        self.assertIn("Back", " ".join(self.labels(mid)))
        for _ in range(4):
            self.tap(A, mid, "⬅")
        self.tap(A, mid, "Review")
        self.tap(A, mid, "GX1")
        self.assertTrue(any(m == "sendDocument" for m, _, _ in self.tg.uploads))
        texts = [m["text"] for m in self.tg.sent]
        self.assertTrue(any("P901 — REPORT: step 3 FAILED" in t for t in texts))
        self.assertIn("sent", self.text_of(mid))
        # every-step set, and a failed album falls back to single files
        self.tg.fail_media = True
        n = len(self.tg.uploads)
        for _ in range(3):
            self.tap(A, mid, "⬅")
        for l in ("🔍", "Screenshots", "GX1", "P902", "Every step"):
            self.tap(A, mid, l)
        docs = [m for m, _, _ in self.tg.uploads[n:] if m == "sendDocument"]
        self.assertEqual(len(docs), 2)  # bn light: steps 001 and 002

    def test_52_one_listener_serves_consent_and_menu(self):
        sb = self.sb
        sb.eng.consent.enabled = lambda: True
        sb.run()
        self.assertEqual(sb.status("GX1.gate.design"), "blocked")
        n = len([c for c in self.tg.calls if c[0] == "getUpdates"])
        ControlTests.uid += 2
        self.tg.updates += [{"update_id": ControlTests.uid - 1, "message": {"from": {"id": int(A)}, "text": "/approve GX1-design 1",
                                                                          "message_id": 9}},
                            {"update_id": ControlTests.uid, "message": {"from": {"id": int(B)}, "text": "/ems", "message_id": 10,
                                                                      "chat": {"id": int(B), "type": "private"}}}]
        sb.eng.consent.poll(force=True)
        self.assertEqual(len([c for c in self.tg.calls if c[0] == "getUpdates"]), n + 1)
        self.assertEqual(len([f for f in os.listdir(sb.c("to-research")) if "Q-consent" in f]), 1)
        self.assertTrue(any("reply_markup" in m and '"m:go:onoff' in m["reply_markup"] for m in self.tg.sent))
        # consent buttons are untouched by the menu
        self.tg.updates.append({"update_id": ControlTests.uid + 1, "callback_query": {"from": {"id": int(B)}, "data": "c|GX1-design|2",
                                                                                   "message": {"message_id": 1}}})
        sb.eng.consent.poll(force=True)

    def test_53_switch_off_ignores_menu_but_not_consent(self):
        sb = self.sb
        sb.eng.consent.enabled = lambda: True
        self.ctl.enabled = lambda: False
        sb.run()
        self.say(A, "ems")
        self.assertFalse(any('"m:' in m.get("reply_markup", "") for m in self.tg.sent))
        self.assertFalse(any(c[0] == "setMyCommands" for c in self.tg.calls))
        self.say(A, "/approve GX1-design 1")
        self.assertEqual(len([f for f in os.listdir(sb.c("to-research")) if "Q-consent" in f]), 1)

    def test_54_cross_notice_log_and_rate_limit(self):
        pause = self.recorder("pause")
        self.say(A, "/ems pause now")
        self.tap(A, self.mid(), "✅")
        notes = [m for m in self.tg.sent if m["chat_id"] == B and "reply_markup" not in m]
        self.assertEqual([m["text"] for m in notes], ["%s paused EMS (now)" % A])
        self.assertFalse([m for m in self.tg.sent if m["chat_id"] == A and m.get("text", "").startswith(A)])
        for _ in range(4):
            self.say(A, "/ems pause")
            self.tap(A, self.mid(), "✅")
        self.assertEqual(pause.call_count, 5)
        self.say(A, "/ems pause")
        self.tap(A, self.mid(), "✅")
        self.assertEqual(pause.call_count, 5)
        self.assertIn("Too many", self.text_of(self.mid()))
        log = open(os.path.join(self.sb.var, "telegram_control.log")).read().splitlines()
        self.assertTrue(all(len(l.split("\t")) == 4 for l in log))
        self.assertTrue(any("\tok: " in l for l in log) and any("rate limit" in l for l in log))
        with unittest.mock.patch("runner.control.now", lambda: time.time() + 61):
            self.say(A, "/ems pause")
            self.tap(A, self.mid(), "✅")
        self.assertEqual(pause.call_count, 6)

    def test_55_typed_shortcuts_and_commands(self):
        self.say(A, "/ems@skybot status")
        self.assertIn("Mode:", self.tg.sent[-1]["text"])
        self.say(A, "/ems duration 2h")
        self.assertIn("Change end from", self.tg.sent[-1]["text"])
        self.assertIn("✅ Run", [b["text"] for b in self.kb(self.mid())])
        self.say(A, "/ems duration 99h")
        self.assertIn("Not understood", self.tg.sent[-1]["text"])
        self.say(A, "/ems resume")  # not paused: not available
        self.assertIn("not available", self.tg.sent[-1]["text"])
        self.say(A, "EMS")
        self.assertIn("EMS · running", self.tg.sent[-1]["text"])
        self.assertEqual(len([c for c in self.tg.calls if c[0] == "setMyCommands"]), 1)
        self.say(A, "emsx")
        self.say(A, "hello")
        n = len(self.tg.sent)
        self.say(A, "ems", date=time.time() - 3600)  # queued while the Mac slept: not answered later
        self.assertEqual(len(self.tg.sent), n)

    def test_56_close_and_back_navigation(self):
        self.say(A, "ems")
        mid = self.mid()
        self.tap(A, mid, "⏻")
        self.assertIn("Stop session now", self.labels(mid))
        self.tap(A, mid, "⬅")
        self.assertIn("✖ Close", self.labels(mid))
        self.tap(A, mid, "✖")
        self.assertIn("Closed", self.text_of(mid))
        self.assertEqual(self.sb.eng.state()["mode"], "running")


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1, argv=[sys.argv[0]])
