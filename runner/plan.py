# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Reads the approved plan order, phase cards, approval files and collab messages for the runner.

import json
import os
import re

from .common import RUNNER_HOME, read_text, sha256_file

HEADER_KEYS = ("from", "to", "date", "about", "needs action", "blocking", "kind", "release tag")
KINDS = ("correction", "answer", "card", "new-requirement", "exception", "refusal", "notice")


def load_sequence(cfg):
    path = os.environ.get("EMS_SEQUENCE_FILE") or cfg.get("paths", "sequence_file") or os.path.join(RUNNER_HOME, "plan", "sequence.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def unit_by_id(seq, uid):
    for u in seq["units"]:
        if u["id"] == uid:
            return u
    return None


def next_unit(seq, uid):
    ids = [u["id"] for u in seq["units"]]
    if uid is None:
        return seq["units"][0]
    i = ids.index(uid)
    return seq["units"][i + 1] if i + 1 < len(ids) else None


class Project(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = cfg.project_root
        self.collab = os.path.join(self.root, cfg.get("paths", "collab_dir", "docs/collab"))

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    # ---- cards and approvals -------------------------------------------------
    def card_path(self, phase):
        if phase == "P000":
            return os.path.join(self.collab, "prompts", "P000.md")
        return os.path.join(self.collab, "phases", "%s.md" % phase)

    def missing_cards(self, unit):
        return [ph for ph in unit["phases"] if not os.path.exists(self.card_path(ph))]

    def approval_path(self, name):
        return os.path.join(self.collab, "approvals", name)

    def approval_ok(self, name):
        """An approval file counts only if it names the owner (OP-Q1 template)."""
        path = self.approval_path(name)
        if not os.path.exists(path):
            return False
        owner = self.cfg.get("project", "owner_name", "Monirul Hasan")
        return owner.lower() in read_text(path).lower()

    def master_sha(self):
        path = self.p(self.cfg.get("paths", "master_plan", "docs/research/EMS_Phase_List_Master.md"))
        return sha256_file(path) if os.path.exists(path) else None

    # ---- messages ------------------------------------------------------------
    def list_messages(self, box):
        d = os.path.join(self.collab, box)
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d) if f.endswith(".md") and not f.startswith("."))

    def read_message(self, box, name):
        text = read_text(os.path.join(self.collab, box, name))
        head = {}
        for line in text.splitlines()[:15]:
            # header lines may carry two fields: "From: Dev      To: Research      Date: ..."
            for m in re.finditer(r"(From|To|Date|About|Needs action|Blocking|Kind|Release tag):\s*(.*?)(?=\s{2,}\w[\w ]*:|$)", line, re.I):
                head[m.group(1).lower()] = m.group(2).strip()
        kind = head.get("kind", "").lower().split()[0] if head.get("kind") else None
        if kind not in KINDS:
            kind = None
        return {"name": name, "box": box, "head": head, "kind": kind, "text": text}

    def last_read_dev(self):
        return read_text(os.path.join(self.collab, ".last_read_dev")).strip()


def msg_refs(msg):
    """Tokens a message refers to: About field ids and file-number stems."""
    about = msg["head"].get("about", "")
    refs = set(re.findall(r"\b[QRDAP]\d{1,4}\b", about))
    refs.update(re.findall(r"\b(\d{4}-\d{2}-\d{2}_\d{3})", msg["text"][:2000]))
    return refs


def msg_id(name):
    m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{3})", name)
    return m.group(1) if m else name
