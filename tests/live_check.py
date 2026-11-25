#!/usr/bin/env python3
# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Live check of the Telegram consent gate: sends a sample gate to the owner ids and waits for a reply. Touches no project files.

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from runner.common import Config, Logger, Store  # noqa: E402
from runner.consent import Consent  # noqa: E402


class FakeProject(object):
    def __init__(self, collab):
        self.collab = collab


def main():
    wait = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    tmp = tempfile.mkdtemp(prefix="ems_live_check_")
    collab = os.path.join(tmp, "collab")
    for d in ("to-research", "to-dev"):
        os.makedirs(os.path.join(collab, d))
    cfg = Config()
    store, log = Store(tmp), Logger(tmp)
    c = Consent(cfg, store, log, FakeProject(collab))
    c.enabled = lambda: True
    ids = [x.strip() for x in (cfg.get("consent", "recipients", "") or "").split(",") if x.strip()]
    c.ids = lambda: ids
    if not c.client():
        print("No TELEGRAM_BOT_TOKEN in ~/.skysoft/secrets.env"); return 1
    task = {"id": "TEST.gate.design", "unit": "TEST", "gate": "TEST_design.md", "title": "LIVE TEST - not a real gate"}
    c.brief_text = lambda t: "This is a live test of the consent gate.\nNothing is approved by answering it.\nReply 1, 2 or 3."
    print("sending to", ids)
    print("sent:", c.send_gate(task))
    end = time.time() + wait
    while time.time() < end:
        c.poll(force=True)
        done = c.state()["gates"].get("TEST-design", {}).get("answered")
        if done:
            print("ANSWER RECEIVED:", done)
            print(open(os.path.join(collab, "to-research", done["file"])).read())
            return 0
        time.sleep(3)
    print("no valid reply within %ds" % wait)
    return 2


if __name__ == "__main__":
    sys.exit(main())
