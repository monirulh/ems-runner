#!/usr/bin/env python3
# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Fake agent CLI for the sandbox tests: replays scripted outcomes per task without any real model use.

import json
import os
import re
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("0.0.0 (fake)")
        return 0
    model = args[args.index("--model") + 1] if "--model" in args else "?"
    task = os.environ.get("EMS_TASK_ID", "?")
    root = os.environ["EMS_PROJECT_ROOT"]
    var = os.environ["EMS_RUNNER_VAR"]
    script = json.load(open(os.environ["FAKE_SCRIPT"]))
    counts_path = os.path.join(var, "fake_counts.json")
    counts = json.load(open(counts_path)) if os.path.exists(counts_path) else {}
    calls_path = os.path.join(var, "fake_calls.jsonl")
    n = counts.get(task, 0)
    counts[task] = n + 1
    json.dump(counts, open(counts_path, "w"))
    with open(calls_path, "a") as f:
        f.write(json.dumps({"task": task, "model": model, "n": n, "args": args}) + "\n")

    steps = None
    for pat, seq in script.items():
        if re.fullmatch(pat, task):
            steps = seq
            break
    step = (steps[n] if n < len(steps) else steps[-1]) if steps else "done"
    emit({"type": "system", "subtype": "init", "session_id": "fake-%s-%d" % (task, n), "model": model})

    def result(text, is_error=False, code=0):
        emit({"type": "result", "subtype": "error" if is_error else "success", "is_error": is_error,
              "result": text, "session_id": "fake", "total_cost_usd": 0.01, "usage": {"input_tokens": 100, "output_tokens": 20}})
        return code

    def write_outputs():
        unit = task.split(".")[0]
        if task.endswith(".design_check"):
            p = os.path.join(root, "docs", "reviews", unit + "_design_check.md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write("check\n")
        elif ".design" in task and ".gate" not in task:
            p = os.path.join(root, "docs", "design", unit + ".md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write("# design %s\n" % unit)
        if re.search(r"\.review\d*$", task):
            p = os.path.join(root, "docs", "reviews", unit + ".md")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "a").write("review\n")
        if task.endswith(".handover"):
            open(os.path.join(root, "docs", "HANDOVER.md"), "w").write("handover %s\n" % task)
        if task.endswith(".plan"):
            ph = task.split(".")[0]
            plan = script.get("__plan__:" + ph) or {"phase": ph, "tasks": [
                {"id": "t01", "kind": "build", "title": "build it", "details": "x", "verify": ["test -d docs"]},
                {"id": "t02", "kind": "test", "title": "test it", "details": "y", "verify": []}]}
            p = os.path.join(var, "plans", ph + ".json")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(plan, open(p, "w"))

    kind = step.split(":")[0]
    if kind == "sleep":
        time.sleep(float(step.split(":")[1]))
        write_outputs()
        return result('ok\nEMS_RESULT {"status": "done", "summary": "slept"}')
    if kind == "done":
        write_outputs()
        return result('work done\nEMS_RESULT {"status": "done", "summary": "fake %s ok"}' % task)
    if kind == "noresult":
        return result("I did something but forgot the result line")
    if kind == "fail":
        return result('EMS_RESULT {"status": "failed", "summary": "%s"}' % (step.split(":", 1)[1] if ":" in step else "tests failed"))
    if kind == "changes":
        write_outputs()
        return result('EMS_RESULT {"status": "changes_required", "summary": "must fix", "items": ["fix A"]}')
    if kind == "blocked":
        _, reason, waiting = step.split(":", 2)
        return result('EMS_RESULT {"status": "blocked", "reason": "%s", "waiting_for": "%s", "summary": "need help"}' % (reason, waiting))
    if kind == "needs_approval":
        return result('EMS_RESULT {"status": "needs_approval", "waiting_for": "%s", "summary": "breaking"}' % step.split(":", 1)[1])
    if kind == "approve":
        write_outputs()
        return result('EMS_RESULT {"status": "approve", "summary": "no deviation"}')
    if kind == "owner":
        write_outputs()
        return result('EMS_RESULT {"status": "owner", "summary": "needs owner", "items": ["OQ-1 new scope"]}')
    if kind == "refused":
        return result('EMS_RESULT {"status": "refused", "summary": "out of scope: page"}')
    if kind == "limit":
        return result(step.split(":", 1)[1], is_error=True, code=1)
    if kind == "auth":
        return result("Login expired · Please run /login", is_error=True, code=1)
    if kind == "network":
        sys.stderr.write("API Error: Unable to connect to API (ECONNREFUSED)\n")
        return 1
    if kind == "service":
        emit({"type": "system", "subtype": "api_retry", "error": "overloaded", "attempt": 10, "max_retries": 10})
        return result("API Error: Repeated 529 Overloaded errors. The API is at capacity", is_error=True, code=1)
    return result("unknown step %s" % step, is_error=True, code=1)


if __name__ == "__main__":
    sys.exit(main())
