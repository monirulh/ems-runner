#!/usr/bin/env python3
# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Pre-tool guard for unattended runs: blocks destructive, out-of-scope and approval-file actions (exit 2).

import json
import os
import re
import sys


def deny(reason):
    sys.stderr.write("BLOCKED by EMS runner guard: %s. Do not retry this action; if it is needed, "
                     "finish with EMS_RESULT status blocked (reason owner-action) and explain why.\n" % reason)
    sys.exit(2)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    root = os.environ.get("EMS_PROJECT_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    role = os.environ.get("EMS_ROLE", "dev")
    task = os.environ.get("EMS_TASK_ID", "")
    skyui_dir = os.path.join(root, "apps", "skyui")

    def rel(p):
        if not p:
            return ""
        ap = os.path.abspath(os.path.join(data.get("cwd") or root, p))
        return ap

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read"):
        path = rel(inp.get("file_path") or inp.get("notebook_path") or "")
        low = path.replace("\\", "/")
        if "/source_snapshot/education" in low:
            deny("reading the Frappe Education snapshot is not allowed (D1, CM-Q4)")
        if re.search(r"(^|/)(\.env(\.local)?|secrets\.env)$", low) or "/.skysoft/" in low:
            deny("secrets files are not opened by agents")
        if tool == "Read":
            return 0
        if "/docs/collab/approvals/" in low:
            deny("approval files are written only by Research on the owner's word (OP-Q1)")
        if "/docs/research/" in low:
            deny("docs/research is owned by Research")
        if re.search(r"/docs/collab/to-[a-z-]+/", low) and os.path.exists(path):
            deny("collab messages are never edited; write a new message file instead (DM-3)")
        if re.search(r"/(frappe-bench/)?apps/(frappe|erpnext|hrms)/", low) or "/vendor/" in low:
            deny("Frappe, ERPNext, HRMS and vendor files are never changed")
        if role == "dev" and low.startswith(skyui_dir.rstrip("/") + "/"):
            deny("EMS Development never changes SkyUI; send an R message to docs/collab/to-skyui/")
        if role == "skyui" and not (low.startswith(skyui_dir.rstrip("/") + "/") or
                                    re.search(r"/docs/collab/to-(research|dev)/", low)):
            deny("SkyUI works only inside apps/skyui (plus new messages to Research and Development)")
        if "/tools/ems-runner/" in low and "/tools/ems-runner/var/plans/" not in low:
            deny("agents do not change the runner itself")
        return 0

    if tool != "Bash":
        return 0
    cmd = inp.get("command", "")
    full = " ".join(cmd.split())
    # text inside here-documents is file content being written, not a command
    body_free = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(\n|$)", "<<HEREDOC\n", cmd, flags=re.S)
    c = " ".join(body_free.split())
    checks = [
        (r"\bsudo\b", "sudo is not used in unattended runs"),
        (r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b", "piping downloads into a shell is not allowed"),
        (r"\bdocker\s+volume\s+(rm|prune)\b", "docker volumes are never removed"),
        (r"\bdocker(-compose|\s+compose)?\b.*\bdown\b.*(-v\b|--volumes)", "docker compose down with volumes is not allowed"),
        (r"\bdocker\s+system\s+prune\b", "docker prune is not allowed"),
        (r"\bbench\b.*\bdrop-site\b", "dropping a site needs the owner"),
        (r"\bbench\b.*\b(reinstall|restore)\b", "reinstall/restore of a site needs the owner"),
        (r"\b(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE)\b", "destructive SQL needs the owner"),
        (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|stash\s+(drop|clear)|branch\s+-D|push\s+.*--force|push\s+-f\b)",
         "git commands that discard work are not allowed"),
        (r"\bgit\s+filter-(branch|repo)\b", "history rewriting is not allowed"),
        (r"\bbench\s+update\b", "no bench update outside a planned upgrade phase"),
        (r"(>|\btee\b|\bcp\b|\bmv\b|\btouch\b|\bsed\s+-i|\brm\b|\bln\b)[^;&|]*docs/collab/approvals",
         "approval files are written only by Research (OP-Q1)"),
        (r"\b(cat|less|more|head|tail|grep|egrep|rg|ag|find|ls|tree|cp|rsync|tar|zip|open|code|sed|awk|wc|diff|python3?|node|cd)\b[^|;&]*source_snapshot/education",
         "the Frappe Education snapshot is not read (D1, CM-Q4)"),
        (r"(secrets\.env|\.skysoft/|infra/\.env|qa/\.env\.local)", "secrets files are not read or printed by agents"),
    ]
    for pat, why in checks:
        if re.search(pat, full if "DROP" in pat else c, re.I):
            deny(why)
    if re.search(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*|-[a-zA-Z]*R)", c):
        targets = [t for t in c.split() if not t.startswith("-") and t not in ("rm",)]
        if not targets or not all(re.match(r"^(\./)?qa/out(/|$)|^/tmp/", t) for t in targets):
            deny("recursive delete is allowed only under qa/out")
    def writes_to(path_re):
        # a write operator aimed at the path inside one command segment (reads like `grep … 2>&1` are fine)
        for seg in re.split(r"\|\||&&|;|\|(?!\|)", c):
            if re.search(r"(?<![0-9&])>>?\s*\S*" + path_re, seg) or re.search(r"\btee\s+(-a\s+)?\S*" + path_re, seg):
                return True
            if re.search(r"\bsed\s+-i\b[^\n]*" + path_re, seg) or re.search(r"\b(touch|truncate|ln)\s[^\n]*" + path_re, seg):
                return True
            m = re.search(r"\b(cp|mv|rsync|install)\s+(.*)$", seg)
            if m and re.search(path_re, m.group(2).split()[-1] if m.group(2).split() else ""):
                return True
        return False
    if writes_to(r"docs/research/"):
        deny("docs/research is owned by Research")
    if re.search(r"\bgit\s+(push|merge)\b", c):
        if not task.endswith(".merge"):
            deny("push and merge happen only in the release merge task")
        unit = task.split(".")[0]
        if not os.path.exists(os.path.join(root, "docs", "collab", "approvals", "%s_release.md" % unit)):
            deny("no release approval file for %s" % unit)
    if role == "dev" and (writes_to(r"apps/skyui/") or
                          re.search(r"\bgit\s+-C\s+\S*apps/skyui\S*\s+(commit|push|merge|rebase|reset|checkout|switch|add|rm|mv|tag|stash|cherry-pick|revert|apply|am)\b", c)):
        deny("EMS Development never changes SkyUI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
