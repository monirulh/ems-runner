# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Starts one non-interactive coding-agent run per task, streams its log and classifies how it ended.

import datetime as dt
import json
import os
import re
import signal
import subprocess
import threading
import time

from .common import now

RESULT_RE = re.compile(r"EMS_RESULT\s*(\{.*\})", re.S)

LIMIT_RE = re.compile(r"hit your (\w+) limit\s*[·•\-–|]?\s*resets\s+([^\n\"]+)", re.I)
LIMIT_LOOSE_RE = re.compile(r"usage limit reached|you've hit your \w+ limit|limit reached\|\d{9,}", re.I)
AUTH_RE = re.compile(r"not logged in|login expired|please run /login|invalid api key|401 invalid authentication|"
                     r"authentication_failed|oauth_org_not_allowed|account_on_hold|billing_error", re.I)
NET_RE = re.compile(r"unable to connect to api|connection refused|connection dropped|no response from api|"
                    r"request timed out|connection lost mid-response|enotfound|econnreset|network is unreachable", re.I)
SERVICE_RE = re.compile(r"api error: (?:request rejected \(429\)|repeated 529|5\d\d)|overloaded|"
                        r"server error mid-response|is experiencing high load|rate_limit|server_error", re.I)

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_reset(text, ref=None):
    """Parse '3:45pm', 'Mon 12:00am', 'Dec 3, 5pm', '3pm (Asia/Dhaka)' -> next local timestamp."""
    ref = ref or now()
    base = dt.datetime.fromtimestamp(ref)
    t = re.sub(r"\(.*?\)", "", text).strip().lower().rstrip(".")
    tm = (re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t) or re.search(r"(\d{1,2}):(\d{2})()", t)
          or re.search(r"(\d{1,2})()()$", t))
    if not tm:
        return None
    hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
    ampm = tm.group(3) or None
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    prefix = t[:tm.start()]
    md = re.search(r"(%s)[a-z]*\.?\s+(\d{1,2})" % "|".join(MONTHS), prefix)
    wd = re.search(r"\b(%s)[a-z]*\b" % "|".join(WEEKDAYS), prefix)
    cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if md:
        month = MONTHS.index(md.group(1)) + 1
        cand = cand.replace(month=month, day=int(md.group(2)))
        if cand <= base:
            cand = cand.replace(year=cand.year + 1)
    elif wd:
        delta = (WEEKDAYS.index(wd.group(1)) - base.weekday()) % 7
        cand = cand + dt.timedelta(days=delta)
        if cand <= base:
            cand += dt.timedelta(days=7)
    elif cand <= base:
        cand += dt.timedelta(days=1)
    return cand.timestamp()


class Outcome(object):
    def __init__(self):
        self.kind = "crash"      # ok | limit | auth | network | service | interrupted | timeout | crash
        self.result = None       # parsed EMS_RESULT dict
        self.text = ""           # final result text
        self.reset_at = None
        self.limit_scope = None  # session | weekly | opus | sonnet | ...
        self.cost = 0.0
        self.usage = {}
        self.session_id = None
        self.exit_code = None
        self.retry_errors = []
        self.detail = ""

    def to_dict(self):
        return {k: getattr(self, k) for k in ("kind", "result", "reset_at", "limit_scope", "cost", "usage",
                                              "session_id", "exit_code", "retry_errors", "detail")}


def parse_result_text(text):
    matches = list(RESULT_RE.finditer(text or ""))
    for m in reversed(matches):
        blob = m.group(1)
        # take the first balanced JSON object
        depth = 0
        for i, ch in enumerate(blob):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                try:
                    return json.loads(blob[:i + 1])
                except ValueError:
                    break
    return None


def classify(out, blob):
    """Decide the outcome kind from the final event and any error text."""
    m = LIMIT_RE.search(blob)
    if m:
        out.kind = "limit"
        out.limit_scope = m.group(1).lower()
        out.reset_at = parse_reset(m.group(2))
        out.detail = m.group(0).strip()
        return
    if LIMIT_LOOSE_RE.search(blob):
        out.kind = "limit"
        epoch = re.search(r"limit reached\|(\d{9,})", blob, re.I)
        out.reset_at = int(epoch.group(1)) if epoch else None
        out.detail = "usage limit reached"
        return
    if AUTH_RE.search(blob):
        out.kind = "auth"
        out.detail = AUTH_RE.search(blob).group(0)
        return
    if NET_RE.search(blob):
        out.kind = "network"
        out.detail = NET_RE.search(blob).group(0)
        return
    if SERVICE_RE.search(blob) or any(e in ("rate_limit", "overloaded", "server_error") for e in out.retry_errors):
        out.kind = "service"
        s = SERVICE_RE.search(blob)
        out.detail = s.group(0) if s else ",".join(out.retry_errors)
        return
    out.kind = "crash"
    out.detail = (blob.strip().splitlines() or ["no output"])[-1][:300]


def build_command(cfg, prompt, model, max_turns, system_file, settings_file=None):
    cli = cfg.agent_cli
    if not cli:
        raise RuntimeError("agent cli_path is not set in ~/.skysoft/ems-runner.ini ([agent] cli_path)")
    cmd = [cli, "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns), "--permission-mode", cfg.get("agent", "permission_mode", "acceptEdits"),
           "--append-system-prompt-file", system_file]
    if settings_file:
        cmd += ["--settings", settings_file]
    allowed = cfg.get("agent", "allowed_tools")
    if allowed:
        cmd += ["--allowedTools", allowed]
    if cfg.getbool("agent", "permission_prompts_none", True):
        cmd += ["--permission-prompts", "none"]
    extra = cfg.get("agent", "extra_args")
    if extra:
        cmd += extra.split()
    return cmd


def run(cfg, log, cmd, cwd, run_log_path, deadline, should_interrupt, env_extra=None):
    """Run the agent; returns Outcome. should_interrupt() -> reason or None (polled every 2 s)."""
    out = Outcome()
    env = dict(os.environ)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["EMS_RUNNER"] = "1"
    env.update(env_extra or {})
    os.makedirs(os.path.dirname(run_log_path), exist_ok=True)
    logf = open(run_log_path, "a", encoding="utf-8")
    stderr_lines = []
    final = {}
    texts = []

    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    except OSError as e:
        out.kind = "crash"
        out.detail = "cannot start agent: %s" % e
        logf.close()
        return out

    def read_stdout():
        for line in proc.stdout:
            logf.write(line)
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                texts.append(line)
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "api_retry":
                out.retry_errors.append(ev.get("error") or "unknown")
            elif ev.get("type") == "system" and ev.get("subtype") == "init":
                out.session_id = ev.get("session_id")
            elif ev.get("type") == "result":
                final.update(ev)
            elif ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))

    def read_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)
            logf.write("[stderr] " + line)

    t1 = threading.Thread(target=read_stdout, daemon=True)
    t2 = threading.Thread(target=read_stderr, daemon=True)
    t1.start()
    t2.start()

    stop_reason = None
    while proc.poll() is None:
        reason = should_interrupt()
        if reason or now() >= deadline:
            stop_reason = reason or "timeout"
            log("stopping agent run (%s): sending SIGINT" % stop_reason)
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except OSError:
                pass
            for _ in range(60):
                if proc.poll() is not None:
                    break
                time.sleep(1)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                for _ in range(30):
                    if proc.poll() is not None:
                        break
                    time.sleep(1)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            break
        time.sleep(0.3)
    proc.wait()
    t1.join(5)
    t2.join(5)
    logf.close()

    out.exit_code = proc.returncode
    out.session_id = final.get("session_id") or out.session_id
    out.cost = final.get("total_cost_usd") or 0.0
    out.usage = final.get("usage") or {}
    out.text = final.get("result") or ""
    if stop_reason:
        out.kind = "timeout" if stop_reason == "timeout" else "interrupted"
        out.detail = stop_reason
        return out
    blob = "\n".join([out.text] + texts[-5:] + stderr_lines[-20:])
    if final and not final.get("is_error") and proc.returncode == 0:
        out.kind = "ok"
        out.result = parse_result_text(out.text) or parse_result_text("\n".join(texts[-3:]))
        if isinstance(final.get("structured_output"), dict):
            out.result = final["structured_output"]
        return out
    classify(out, blob)
    return out
