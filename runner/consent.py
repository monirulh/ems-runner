# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Telegram consent gate (TG-CONSENT-APPROVE): sends the brief and options to the owner and turns a valid reply into an answer file for Research.

import json
import os
import re
import urllib.parse
import urllib.request

from .common import iso, load_secrets, now, read_text

PART_MAX = 3800  # Telegram limit is 4096; leave room for the part label
OPTIONS = [
    "Approve (recommended)",
    "Do not approve: changes needed (write them to Research)",
    "Hold: discuss in the Research chat first",
]


def gate_code(task):
    """Short code shown on the phone, e.g. G01-design or G01-release."""
    kind = "release" if task["id"].endswith(".gate.release") else "design"
    return "%s-%s" % (task["unit"], kind)


def split_parts(text, size=PART_MAX):
    text = (text or "").strip() or "(no brief text found)"
    parts, cur = [], ""
    for para in text.split("\n"):
        while len(para) > size:  # one very long line
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:size])
            para = para[size:]
        if len(cur) + len(para) + 1 > size:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


class Telegram(object):
    """Thin Bot API client. Tests replace `call`."""

    def __init__(self, token):
        self.token = token

    def call(self, method, params):
        data = urllib.parse.urlencode(params).encode()
        raw = urllib.request.urlopen("https://api.telegram.org/bot%s/%s" % (self.token, method), data=data, timeout=40).read()
        return json.loads(raw)


class Consent(object):
    def __init__(self, cfg, store, log, project, client=None):
        self.cfg, self.store, self.log, self.project = cfg, store, log, project
        self._client = client
        self._last_poll = 0

    # ---------------------------------------------------------------- settings
    def enabled(self):
        return self.cfg.getbool("consent", "telegram", False)

    def ids(self):
        raw = self.cfg.get("consent", "recipients", "") or load_secrets().get("TELEGRAM_CONSENT_IDS", "")
        return [x.strip() for x in str(raw).replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()]

    def client(self):
        if self._client:
            return self._client
        token = load_secrets().get("TELEGRAM_BOT_TOKEN")
        return Telegram(token) if token else None

    def state(self):
        return self.store.load("consent.json", {"offset": 0, "gates": {}})

    # ---------------------------------------------------------------- sending
    def brief_text(self, task):
        unit, design = task["unit"], not task["id"].endswith(".gate.release")
        root = self.cfg.project_root
        names = ["docs/design/%s.md" % unit] if design else ["docs/reviews/%s.md" % unit, "docs/design/%s.md" % unit]
        if design:
            names.append("docs/reviews/%s_design_check.md" % unit)
        out = ["Gate: %s" % task.get("gate", ""), "Task: %s" % task.get("title", "")]
        for n in names:
            body = read_text(os.path.join(root, n))
            if body.strip():
                out.append("\n===== %s =====\n%s" % (n, body))
        return "\n".join(out)

    def send_gate(self, task):
        """Park notice for one consent gate. Returns True if sent to at least one recipient. Never raises."""
        if not self.enabled():
            return False
        code = gate_code(task)
        try:
            tg, ids = self.client(), self.ids()
            if not tg or not ids:
                self.log("consent: no bot token or recipients; gate %s not sent" % code)
                return False
            with self.store.locked():
                st = self.state()
                if code in st["gates"]:
                    return True  # already sent once
                st["gates"][code] = {"gate": task["gate"], "task": task["id"], "unit": task["unit"],
                                     "options": OPTIONS, "answered": None, "sent": iso(), "msgs": {}}
                self.store.save("consent.json", st)
            parts = split_parts(self.brief_text(task))
            n = len(parts)
            keyboard = json.dumps({"inline_keyboard": [[{"text": "%d  %s" % (i + 1, o[:40]),
                                                         "callback_data": "c|%s|%d" % (code, i + 1)}] for i, o in enumerate(OPTIONS)]})
            header = ("CONSENT NEEDED %s\n%s\nBrief follows in %d part(s). Reply with an option number to this message, "
                      "tap a button, or send /approve %s <number>.\n\n%s" %
                      (code, task.get("title", ""), n, code,
                       "\n".join("%d. %s" % (i + 1, o) for i, o in enumerate(OPTIONS))))
            sent = {}
            for chat in ids:
                try:
                    r = tg.call("sendMessage", {"chat_id": chat, "text": header[:4000], "reply_markup": keyboard})
                    sent[chat] = (r.get("result") or {}).get("message_id")
                    for i, p in enumerate(parts, 1):
                        tg.call("sendMessage", {"chat_id": chat, "text": "[%s %d/%d]\n%s" % (code, i, n, p)})
                except Exception as e:  # noqa: BLE001
                    self.log("consent: send to %s failed: %s" % (chat, type(e).__name__))
            with self.store.locked():
                st = self.state()
                st["gates"][code]["msgs"] = sent
                self.store.save("consent.json", st)
            self.log("consent: sent %s to %d recipient(s)" % (code, len(sent)), kind="consent", gate=code)
            return bool(sent)
        except Exception as e:  # noqa: BLE001
            self.log("consent: send failed: %s" % type(e).__name__)
            return False

    # ---------------------------------------------------------------- replies
    def poll(self, force=False):
        """Fetch updates and handle replies. Throttled; never raises."""
        if not self.enabled():
            return 0
        interval = self.cfg.getint("consent", "poll_seconds", 5)
        if not force and now() - self._last_poll < interval:
            return 0
        self._last_poll = now()
        st = self.state()
        if not any(g["answered"] is None for g in st["gates"].values()):
            return 0  # nothing pending: no need to call Telegram
        try:
            tg = self.client()
            if not tg:
                return 0
            r = tg.call("getUpdates", {"offset": st["offset"], "timeout": 0,
                                       "allowed_updates": json.dumps(["message", "callback_query"])})
            handled = 0
            for upd in r.get("result", []):
                self.handle(upd, tg)
                handled += 1
                with self.store.locked():
                    s = self.state()
                    s["offset"] = max(s["offset"], upd["update_id"] + 1)
                    self.store.save("consent.json", s)
            return handled
        except Exception as e:  # noqa: BLE001
            self.log("consent: poll failed: %s" % type(e).__name__)
            return 0

    def handle(self, upd, tg=None):
        """Returns the answer file name if the update was a valid reply, else None."""
        allowed = self.ids()
        cb = upd.get("callback_query")
        msg = upd.get("message")
        if cb:
            sender, text, mid = str((cb.get("from") or {}).get("id", "")), "", (cb.get("message") or {}).get("message_id")
            m = re.match(r"c\|([\w.-]+)\|(\d+)$", cb.get("data") or "")
            code, opt = (m.group(1), int(m.group(2))) if m else (None, None)
        elif msg:
            sender, mid = str((msg.get("from") or {}).get("id", "")), msg.get("message_id")
            text = (msg.get("text") or "").strip()
            code, opt = None, None
            m = re.match(r"/approve(?:@\w+)?\s+([\w.-]+)\s+(\d+)\s*$", text, re.I)
            if m:
                code, opt = m.group(1), int(m.group(2))
            elif re.match(r"\d+$", text):
                opt = int(text)
                reply_to = (msg.get("reply_to_message") or {}).get("message_id")
                if reply_to is not None:
                    for c, g in self.state()["gates"].items():
                        if reply_to in [v for v in g["msgs"].values()]:
                            code = c
        else:
            return None
        if sender not in allowed:
            self.log("consent: ignored reply from a sender not on the list")
            return None
        if not code or opt is None:
            return None
        return self.answer(code, opt, sender, mid, tg)

    def answer(self, code, opt, sender, mid, tg=None):
        with self.store.locked():
            st = self.state()
            key = next((k for k in st["gates"] if k.lower() == code.lower()), None)
            g = st["gates"].get(key) if key else None
            if not g or g["answered"] is not None or not 1 <= opt <= len(g["options"]):
                self.log("consent: rejected reply for %s option %s" % (code, opt))
                return None
            name = self.write_answer(key, g, opt, sender, mid)
            g["answered"] = {"option": opt, "by": sender, "at": iso(), "file": name}
            self.store.save("consent.json", st)
        self.log("consent: answer %s option %d written as %s" % (key, opt, name), kind="consent", gate=key)
        try:
            tg = tg or self.client()
            for chat in self.ids():
                tg.call("sendMessage", {"chat_id": chat, "text": "%s: option %d recorded and sent to Research. The runner "
                                        "continues when Research writes the approval file." % (key, opt)})
        except Exception:  # noqa: BLE001
            pass
        return name

    def write_answer(self, code, g, opt, sender, mid):
        collab = self.project.collab
        top = 0
        for box in ("to-research", "to-dev"):
            d = os.path.join(collab, box)
            for f in os.listdir(d) if os.path.isdir(d) else []:
                m = re.match(r"\d{4}-\d{2}-\d{2}_(\d+)_", f)
                if m:
                    top = max(top, int(m.group(1)))
        date = iso()[:10]
        name = "%s_%03d_Q-consent-%s.md" % (date, top + 1, code)
        path = os.path.join(collab, "to-research", name)
        body = ("Copyright (c) 2026 Monirul Hasan. All rights reserved.\n"
                "<!-- Owner answer to a consent gate, received by Telegram (TG-CONSENT-APPROVE). -->\n\n"
                "# Consent answer %s\n"
                "From: Runner      To: Research      Date: %s\n"
                "About: %s (gate %s), task %s\n"
                "Needs action: yes            Blocking: yes\n"
                "Kind: answer\n\n"
                "The owner chose option %d: %s.\n\n"
                "- Task code: %s\n- Option: %d\n- Telegram sender ID: %s\n- Telegram message ID: %s\n- Received: %s\n\n"
                "Research: record this as the owner's decision and, for option 1, write docs/collab/approvals/%s "
                "(TG-CONSENT-Q1). The runner resumes when that file exists.\n" %
                (code, date, g["unit"], g["gate"], g["task"], opt, g["options"][opt - 1], code, opt, sender, mid, iso(), g["gate"]))
        with open(path, "x") as f:  # 'x': never overwrite an existing message
            f.write(body)
        return name
