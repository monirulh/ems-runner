# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Telegram control menu for the EMS runner (TG-CONTROL): nested buttons, confirm screens, shared actions, screenshots and reviews.

import datetime as dt
import json
import os
import re
import secrets

from . import actions, shots
from .common import now, parse_until

MENU_TTL = 600           # a menu stops working 10 minutes after it was last drawn
MAX_CHANGES_PER_MIN = 5
CUSTOM_RE = re.compile(r"^\d{1,2}h(\d{1,2}m)?$|^\d{1,3}m$|^([01]\d|2[0-3]):[0-5]\d$")
EMS_RE = re.compile(r"^/?ems(?:@\w+)?(?:\s+(.*))?$", re.I | re.S)
DEFAULT_PRESETS = "1h, 2h, 3h, 5h, 08:00, 12:00"
GROUP_BUTTONS = 12


def parse_custom(text):
    """'2h30m' | '90m' | '06:30' -> spec ('m150' / 'u0630'), or None if not accepted (1 min to 24 h)."""
    t = (text or "").strip().lower()
    if not CUSTOM_RE.fullmatch(t):
        return None
    if ":" in t:
        return "u" + t.replace(":", "")
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", t)
    minutes = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return "m%d" % minutes if 1 <= minutes <= 1440 else None


def spec_text(spec):
    """'m150' -> '150m' (duration text for the shared actions); 'u0630' -> '06:30'."""
    if spec[:1] == "m" and spec[1:].isdigit():
        return "%dm" % int(spec[1:])
    if spec[:1] == "u" and len(spec) == 5 and spec[1:].isdigit():
        return "%s:%s" % (spec[1:3], spec[3:])
    return None


def spec_label(spec, clock_prefix="Until "):
    t = spec_text(spec)
    if t is None:
        return spec
    if ":" in t:
        return clock_prefix + t
    mins = int(t[:-1])
    h, m = divmod(mins, 60)
    return ("%d h" % h if not m else "%d h %d min" % (h, m)) if h else "%d min" % m


def preset_to_spec(item):
    item = item.strip().lower().replace("until", "").strip()
    if ":" in item:
        return parse_custom(item) if re.fullmatch(r"\d\d:\d\d", item) else None
    if re.fullmatch(r"\d{1,2}h", item) or re.fullmatch(r"\d{1,3}m", item):
        return parse_custom(item)
    return None


def clock(ts):
    d = dt.datetime.fromtimestamp(ts)
    today = dt.datetime.fromtimestamp(now()).date()
    return d.strftime("%H:%M") if d.date() == today else d.strftime("%a %H:%M")


def rows(buttons, per):
    return [buttons[i:i + per] for i in range(0, len(buttons), per)]


class Control(object):
    def __init__(self, eng, client=None):
        self.eng = eng
        self._client = client
        self._setup_done = False

    # ---------------------------------------------------------------- settings and plumbing
    def enabled(self):
        return self.eng.cfg.getbool("control", "telegram", False)

    def ids(self):
        return self.eng.consent.ids()

    def client(self):
        return self._client or self.eng.consent.client()

    def store_state(self):
        return self.eng.store.load("telegram_control.json", {"menus": {}, "awaiting": {}, "changes": []})

    def presets(self):
        items = self.eng.cfg.getlist("control", "presets", DEFAULT_PRESETS.split(","))
        specs = [s for s in (preset_to_spec(i) for i in items) if s]
        default = preset_to_spec(self.eng.cfg.get("control", "default_preset", "5h") or "5h")
        if default in specs:
            specs.remove(default)
        return ([default] if default else []) + specs, default

    def log_action(self, uid, action, result):
        try:
            line = "%s\t%s\t%s\t%s\n" % (dt.datetime.fromtimestamp(now()).strftime("%Y-%m-%d %H:%M:%S"), uid,
                                        str(action).replace("\n", " ")[:120], str(result).replace("\n", " ")[:200])
            with open(os.path.join(self.eng.cfg.var_dir, "telegram_control.log"), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def setup(self, tg):
        """Register /ems once per daemon start."""
        if self._setup_done:
            return
        self._setup_done = True
        try:
            tg.call("setMyCommands", {"commands": json.dumps([{"command": "ems", "description": "Open the EMS runner menu"}])})
        except Exception as e:  # noqa: BLE001
            self.eng.log("control: setMyCommands failed: %s" % type(e).__name__)

    def tg_try(self, method, params):
        try:
            return self.client().call(method, params)
        except Exception as e:  # noqa: BLE001
            self.eng.log("control: %s failed: %s" % (method, type(e).__name__))
            return None

    # ---------------------------------------------------------------- state
    def snap(self):
        st = self.eng.state()
        on = bool(st.get("session")) and st["mode"] != "asleep"
        return st, on, st["mode"] == "paused"

    def sig(self):
        st, on, paused = self.snap()
        return [st["mode"], on, int((st.get("session") or {}).get("end", 0))]

    def state_line(self):
        st, on, paused = self.snap()
        if not on:
            return "EMS · off"
        parts = ["EMS", "paused" if paused else ("waiting" if st["mode"] == "waiting" else "running")]
        cur = st.get("current")
        if cur:
            parts.append("%s (%s)" % (cur["task"], str(cur["model"]).capitalize()))
        parts.append("ends %s" % clock(st["session"]["end"]))
        return " · ".join(parts)

    def allowed(self, path):
        st, on, paused = self.snap()
        top = path.split(".")
        if top[0] == "onoff" and len(top) > 1:
            return (not on) if top[1] == "start" else on if top[1] == "stop" else False
        if top[0] == "pr" and len(top) == 1:
            return on
        if top[0] == "pr":
            return (on and not paused) if top[1] in ("task", "now") else paused if top[1] == "resume" else False
        if top[0] == "dur":
            return on
        return True

    # ---------------------------------------------------------------- screens
    def groups(self):
        out = []
        for u in reversed(self.eng.seq["units"]):
            phases = [p for p in u["phases"] if shots.latest_run(self.eng.cfg.project_root, p)]
            if phases:
                out.append((u["id"], phases))
        return out[:GROUP_BUTTONS]

    def confirm_text(self, path):
        st, on, paused = self.snap()
        p = path.split(".")
        if p[0] == "onoff" and p[1] == "start":
            return "Start a session until %s?" % self.end_of(p[2])
        if p[0] == "onoff" and p[1] == "stop":
            return "Stop the session now? The running task is checkpointed."
        if p[0] == "pr":
            return {"task": "Pause after the current task?", "now": "Pause now? The running task is interrupted and checkpointed.",
                    "resume": "Resume the session?"}[p[1]]
        if p[0] == "dur":
            return "Change end from %s to %s?" % (clock(st["session"]["end"]), self.end_of(p[1]))
        return "Run?"

    def end_of(self, spec):
        t = spec_text(spec)
        if t is None:
            return "?"
        return clock(parse_until(t) if ":" in t else now() + int(t[:-1]) * 60)

    def screen(self, path):
        """-> (text, rows of (label, action, arg)). Unknown or not allowed path -> home."""
        p = path.split(".") if path != "home" else ["home"]
        st, on, paused = self.snap()
        head = self.state_line()
        back = lambda to: [("⬅ Back", "go", to)]  # noqa: E731
        parent = ".".join(p[:-1]) or "home"
        if p[0] == "home":
            r = [[("⏻ On / Off", "go", "onoff")]]
            if on:
                r.append([("⏯ Pause / Resume", "go", "pr")])
            r.append([("📊 Status", "go", "status")])
            if on:
                r.append([("⏱ Duration", "go", "dur")])
            r += [[("🔍 Review & screenshots", "go", "rv")], [("✖ Close", "x", "-")]]
            return head, r
        if not self.allowed(path):
            return head + "\nThat is not available in the current state.", [[("⬅ Menu", "go", "home")]]
        if p[0] == "onoff" and len(p) == 1:
            if on:
                return head, [[("Stop session now", "go", "onoff.stop")], back("home")]
            return head, [[("Start session", "go", "onoff.start")], back("home")]
        if p[0] == "pr" and len(p) == 1:
            if paused:
                return head, [[("Resume", "go", "pr.resume")], back("home")]
            return head, [[("Pause after this task", "go", "pr.task")], [("Pause now", "go", "pr.now")], back("home")]
        if (p[0] == "onoff" and p[1:2] == ["start"] and len(p) == 2) or (p[0] == "dur" and len(p) == 1):
            specs, default = self.presets()
            base = path
            sep = "." if len(p) > 0 else ""
            btns = [("%s%s" % (spec_label(s), " (default)" if s == default and p[0] == "onoff" else ""), "go", base + sep + s)
                    for s in specs]
            btns.append(("Custom…", "go", base + sep + "cust"))
            title = "How long should the session run?" if p[0] == "onoff" else "New end of the session, counted from now:"
            return head + "\n" + title, rows(btns, 3) + [back(parent)]
        if (p[0] == "onoff" and p[1:2] == ["start"] and len(p) == 3) or (p[0] == "dur" and len(p) == 2) or \
                (p[0] == "onoff" and p[1:2] == ["stop"]) or p[0] == "pr":
            return head + "\n" + self.confirm_text(path), [[("✅ Run", "run", path), ("⬅ Back", "go", parent)]]
        if p[0] == "status":
            body = actions.status_text(self.eng)
            return (head + "\n\n" + body)[:3900], [[("🔄 Refresh", "go", "status"), ("⬅ Back", "go", "home")]]
        if p[0] == "rv":
            return self.rv_screen(p, head)
        return self.screen("home")

    def rv_screen(self, p, head):
        groups = self.groups()
        if len(p) == 1:
            return head + "\nReview & screenshots", [[("Review a group", "go", "rv.r")], [("Screenshots", "go", "rv.s")],
                                                      [("⬅ Back", "go", "home")]]
        kind = p[1]
        if len(p) == 2:
            if not groups:
                return head + "\nNo QA runs yet.", [[("⬅ Back", "go", "rv")]]
            return (head + "\nPick a group (newest first):",
                    rows([(g, "go", "rv.%s.%s" % (kind, g)) for g, _ in groups], 3) + [[("⬅ Back", "go", "rv")]])
        gid = p[2]
        phases = dict(groups).get(gid)
        if phases is None:
            return head + "\nNo QA run for %s." % gid, [[("⬅ Back", "go", "rv.%s" % kind)]]
        if kind == "s" and len(p) == 3:
            return head + "\nPick a phase of %s:" % gid, rows([(ph, "go", "rv.s.%s.%s" % (gid, ph)) for ph in phases], 3) + \
                [[("⬅ Back", "go", "rv.s")]]
        if kind == "s" and len(p) == 4:
            base = ".".join(p)
            return head + "\n%s %s: which set?" % (gid, p[3]), [[("Every step, Bangla light", "go", base + ".bn")],
                                                               [("Key screens, all 6", "go", base + ".k6")],
                                                               [("⬅ Back", "go", "rv.s.%s" % gid)]]
        return head, [[("⬅ Back", "go", "rv")]]

    def is_sender(self, path):
        """Read-only paths that run at once (send files/pictures)."""
        p = path.split(".")
        return p[0] == "rv" and ((p[1:2] == ["r"] and len(p) == 3) or (p[1:2] == ["s"] and len(p) == 5 and p[4] in ("bn", "k6")))

    # ---------------------------------------------------------------- drawing
    def keyboard(self, rws, rec):
        out = []
        for row in rws:
            line = []
            for label, action, arg in row:
                nonce = secrets.token_hex(3)
                data = "m:%s:%s:%s" % (action, arg, nonce)
                assert len(data.encode()) <= 64, data
                rec["nonces"][nonce] = [action, arg]
                line.append({"text": label, "callback_data": data})
            out.append(line)
        return json.dumps({"inline_keyboard": out})

    def draw(self, chat, mid, path, note=None, text=None, rws=None):
        """Send (mid None) or edit the menu message for `path` with fresh single-use buttons."""
        if text is None:
            text, rws = self.screen(path)
        if note:
            text = note + "\n\n" + text
        rec = {"t": now(), "nonces": {}, "sig": self.sig()}
        markup = self.keyboard(rws, rec)
        params = {"chat_id": chat, "text": text[:4000], "reply_markup": markup}
        if mid is None:
            r = self.tg_try("sendMessage", params)
            mid = ((r or {}).get("result") or {}).get("message_id")
        else:
            params["message_id"] = mid
            self.tg_try("editMessageText", params)
        if mid is not None:
            with self.eng.store.locked():
                s = self.store_state()
                s["menus"]["%s:%s" % (chat, mid)] = rec
                cutoff = now() - 3600
                s["menus"] = {k: v for k, v in s["menus"].items() if v["t"] > cutoff}
                self.eng.store.save("telegram_control.json", s)
        return mid

    # ---------------------------------------------------------------- incoming updates
    def claims(self, upd):
        cb, msg = upd.get("callback_query"), upd.get("message")
        if cb:
            return str(cb.get("data") or "").startswith("m:")
        if msg:
            text = (msg.get("text") or "").strip()
            if EMS_RE.match(text):
                return True
            uid = str((msg.get("from") or {}).get("id", ""))
            return uid in self.store_state()["awaiting"] and not text.startswith("/")
        return False

    def handle(self, upd):
        try:
            self._handle(upd)
        except Exception as e:  # noqa: BLE001
            self.eng.log("control: error %s" % type(e).__name__)
        return True

    def _handle(self, upd):
        cb, msg = upd.get("callback_query"), upd.get("message")
        src = cb or msg
        uid = str((src.get("from") or {}).get("id", ""))
        m = (cb or {}).get("message") or msg or {}
        chat = m.get("chat") or {}
        if uid not in self.ids():
            self.log_action(uid, "ignored", "sender not on the list")
            return
        if chat.get("type") != "private" or str(chat.get("id")) != uid:
            self.log_action(uid, "ignored", "not a private chat")
            return
        cid = chat["id"]
        if cb:
            return self.on_callback(cb, uid, cid, m.get("message_id"))
        if now() - float(msg.get("date") or now()) > MENU_TTL:
            self.log_action(uid, "ignored", "old message")
            return
        self.on_text(msg, uid, cid)

    def answer(self, cb, text=""):
        self.tg_try("answerCallbackQuery", {"callback_query_id": cb.get("id", ""), "text": text[:150]})

    def on_callback(self, cb, uid, cid, mid):
        parts = str(cb.get("data") or "").split(":")
        if len(parts) != 4 or parts[0] != "m":
            return
        _, action, arg, nonce = parts
        key = "%s:%s" % (cid, mid)
        with self.eng.store.locked():
            s = self.store_state()
            rec = s["menus"].get(key)
            refusal = None
            if not rec:
                refusal = "This menu has expired. Send ems to open a new one."
            elif now() - rec["t"] > MENU_TTL:
                refusal = "This menu has expired. Send ems to open a new one."
                s["menus"].pop(key, None)
            elif rec["nonces"].get(nonce) != [action, arg]:
                refusal = "That button was already used. Use the buttons below or send ems."
            if refusal:
                self.eng.store.save("telegram_control.json", s)
            else:
                rec["nonces"] = {}  # single use: all buttons of this drawing stop working
                sig = rec["sig"]
                s["awaiting"].pop(uid, None)
                self.eng.store.save("telegram_control.json", s)
        if refusal:
            self.log_action(uid, "%s:%s" % (action, arg), "refused: " + refusal)
            self.answer(cb, refusal)
            return
        self.answer(cb)
        if action == "x":
            with self.eng.store.locked():
                s = self.store_state()
                s["menus"].pop(key, None)
                self.eng.store.save("telegram_control.json", s)
            self.tg_try("editMessageText", {"chat_id": cid, "message_id": mid, "text": "Closed. Send ems to open the menu again."})
            self.log_action(uid, "close", "ok")
        elif action == "go":
            self.on_go(uid, cid, mid, arg)
        elif action == "run":
            self.on_run(uid, cid, mid, arg, sig)

    def on_go(self, uid, cid, mid, path):
        p = path.split(".")
        if p[-1] == "cust" and p[0] in ("onoff", "dur") and self.allowed(path):
            with self.eng.store.locked():
                s = self.store_state()
                s["awaiting"][uid] = {"chat": cid, "mid": mid, "base": ".".join(p[:-1]), "t": now()}
                self.eng.store.save("telegram_control.json", s)
            self.draw(cid, mid, path, text="Send the time as one reply: 2h30m, 90m or a clock time like 06:30 (1 minute to 24 hours).",
                      rws=[[("⬅ Back", "go", ".".join(p[:-1]))]])
            self.log_action(uid, "custom", "waiting for a reply")
            return
        if self.is_sender(path):
            note = self.send_content(uid, cid, p)
            self.draw(cid, mid, path, text=note, rws=[[("⬅ Back", "go", ".".join(p[:-1]))]])
            return
        self.draw(cid, mid, path)
        self.log_action(uid, "go " + path, "shown")

    def on_run(self, uid, cid, mid, path, sig):
        if not self.allowed(path) or sig != self.sig():
            self.draw(cid, mid, "home", note="The state changed since this menu was drawn, so nothing was run.")
            self.log_action(uid, "run " + path, "refused: state changed")
            return
        with self.eng.store.locked():
            s = self.store_state()
            recent = [t for t in s["changes"] if now() - t < 60]
            if len(recent) >= MAX_CHANGES_PER_MIN:
                self.eng.store.save("telegram_control.json", s)
                refused = True
            else:
                s["changes"] = recent + [now()]
                self.eng.store.save("telegram_control.json", s)
                refused = False
        if refused:
            self.draw(cid, mid, "home", note="Too many changes in one minute (limit %d). Wait a little." % MAX_CHANGES_PER_MIN)
            self.log_action(uid, "run " + path, "refused: rate limit")
            return
        ok, text, what = self.execute(path)
        self.log_action(uid, "run " + path, ("ok: " if ok else "failed: ") + text)
        self.draw(cid, mid, "home", note=("Done: " if ok else "Not done: ") + text)
        if ok:
            for other in self.ids():
                if other != uid:
                    self.tg_try("sendMessage", {"chat_id": other, "text": "%s %s" % (uid, what)})

    def execute(self, path):
        """Run one confirmed action through the shared functions. -> (ok, text, notice)."""
        p, e = path.split("."), self.eng
        if p[0] == "onoff" and p[1] == "start":
            t = spec_text(p[2])
            ok, text = actions.start(e, duration=None if ":" in t else t, until=t if ":" in t else None)
            return ok, text, "started EMS"
        if p[0] == "onoff" and p[1] == "stop":
            ok, text = actions.stop(e)
            return ok, text, "stopped EMS"
        if p[0] == "pr" and p[1] == "task":
            ok, text = actions.pause(e, False)
            return ok, text, "paused EMS (after task)"
        if p[0] == "pr" and p[1] == "now":
            ok, text = actions.pause(e, True)
            return ok, text, "paused EMS (now)"
        if p[0] == "pr" and p[1] == "resume":
            ok, text = actions.resume(e)
            return ok, text, "resumed EMS"
        if p[0] == "dur":
            ok, text = actions.set_duration(e, spec_text(p[1]), from_now=True)
            return ok, text, "changed the session end"
        return False, "unknown action", ""

    # ---------------------------------------------------------------- typed text
    def on_text(self, msg, uid, cid):
        text = (msg.get("text") or "").strip()
        m = EMS_RE.match(text)
        if not m:
            return self.on_custom_reply(text, uid, cid)
        with self.eng.store.locked():
            s = self.store_state()
            s["awaiting"].pop(uid, None)
            self.eng.store.save("telegram_control.json", s)
        args = (m.group(1) or "").split()
        if not args:
            self.draw(cid, None, "home")
            self.log_action(uid, "ems", "menu opened")
            return
        cmd, rest = args[0].lower(), args[1:]
        path = None
        if cmd == "status":
            path = "status"
        elif cmd == "stop":
            path = "onoff.stop"
        elif cmd == "resume":
            path = "pr.resume"
        elif cmd == "pause":
            path = "pr.now" if rest[:1] == ["now"] else "pr.task"
        elif cmd in ("start", "duration"):
            spec = parse_custom(rest[0]) if rest else (self.presets()[1] if cmd == "start" else None)
            if spec:
                path = ("onoff.start." if cmd == "start" else "dur.") + spec
        if path is None:
            self.tg_try("sendMessage", {"chat_id": cid, "text": "Not understood. Use /ems, /ems status, /ems pause [now], "
                                        "/ems resume, /ems stop, /ems start [2h], /ems duration 2h (1 min to 24 h or hh:mm)."})
            self.log_action(uid, "ems " + " ".join(args), "refused: not understood")
            return
        if not self.allowed(path):
            self.draw(cid, None, "home", note="That is not available in the current state.")
        else:
            self.draw(cid, None, path)
        self.log_action(uid, "ems " + " ".join(args), "shown " + path)

    def on_custom_reply(self, text, uid, cid):
        with self.eng.store.locked():
            s = self.store_state()
            aw = s["awaiting"].get(uid)
            if aw and now() - aw["t"] > MENU_TTL:
                s["awaiting"].pop(uid, None)
                self.eng.store.save("telegram_control.json", s)
                aw = None
        if not aw:
            return
        spec = parse_custom(text)
        if not spec:
            self.tg_try("sendMessage", {"chat_id": cid, "text": "Not accepted. Send one value like 2h30m, 90m or 06:30 "
                                        "(1 minute to 24 hours), or tap Back."})
            self.log_action(uid, "custom", "refused: bad value")
            return
        with self.eng.store.locked():
            s = self.store_state()
            s["awaiting"].pop(uid, None)
            self.eng.store.save("telegram_control.json", s)
        self.draw(aw["chat"], aw["mid"], "%s.%s" % (aw["base"], spec))
        self.log_action(uid, "custom", "accepted " + spec)

    # ---------------------------------------------------------------- review and screenshots
    def send_content(self, uid, cid, p):
        """Send a review (rv.r.G01) or a screenshot set (rv.s.G01.P003.bn|k6). -> note for the menu."""
        try:
            if p[1] == "r":
                note = self.send_review(cid, p[2])
            else:
                note = self.send_set(cid, p[3], p[4])
        except Exception as e:  # noqa: BLE001
            note = "Could not send: %s" % type(e).__name__
        self.log_action(uid, "send " + ".".join(p), note.replace("\n", " | "))
        return note

    def send_review(self, cid, group):
        ok, text, info = actions.review(self.eng, group)
        if not ok:
            return text
        probs = info["problems"]
        body = ("Review %s: %d problem(s)\n" % (group, len(probs)) + "\n".join("- " + x for x in probs)) if probs else \
            "Review %s: no problems reported." % group
        self.tg_try("sendMessage", {"chat_id": cid, "text": body[:4000]})
        self.upload_one(cid, "sendDocument", "document", info["path"], "Review page %s (open on the Mac for the pictures)" % group)
        key = []
        for ph in info["phases"]:
            key += shots.key_bn_light(shots.list_shots(shots.latest_run(self.eng.cfg.project_root, ph)))
        notes = self.send_shots(cid, key)
        return "Review %s sent: problems, page%s." % (group, ", key screens" if key else "") + notes

    def send_set(self, cid, phase, which):
        run = shots.latest_run(self.eng.cfg.project_root, phase)
        allshots = shots.list_shots(run)
        if not allshots:
            return "No screenshots found for %s." % phase
        chosen = shots.every_step_bn_light(allshots) if which == "bn" else shots.key_all6(allshots)
        if not chosen:
            return "No matching screenshots for %s." % phase
        return "%s: %s" % (phase, "every step, Bangla light" if which == "bn" else "key screens, all 6") + self.send_shots(cid, chosen)

    def upload_one(self, cid, method, field, path, caption):
        try:
            self.client().upload(method, {"chat_id": cid, "caption": caption[:1000]}, {field: path})
            return True
        except Exception as e:  # noqa: BLE001
            self.eng.log("control: %s failed: %s" % (method, type(e).__name__))
            return False

    def send_shots(self, cid, chosen):
        albums, big, rest = shots.plan_albums(chosen)
        sent = 0
        for album in albums:
            if len(album) == 1:
                ok = self.upload_one(cid, "sendPhoto", "photo", album[0]["path"], shots.caption(album[0]))
            else:
                media, files = [], {}
                for i, s in enumerate(album):
                    media.append({"type": "photo", "media": "attach://f%d" % i, "caption": shots.caption(s)})
                    files["f%d" % i] = s["path"]
                try:
                    self.client().upload("sendMediaGroup", {"chat_id": cid, "media": json.dumps(media)}, files)
                    ok = True
                except Exception as e:  # noqa: BLE001
                    self.eng.log("control: sendMediaGroup failed: %s" % type(e).__name__)
                    ok = all([self.upload_one(cid, "sendDocument", "document", s["path"], shots.caption(s)) for s in album])
            sent += len(album) if ok else 0
        note = "\n%d picture(s) sent." % sent
        if big:
            note += "\n%d picture(s) over 10 MB skipped." % len(big)
        if rest:
            note += "\n%d more not sent (limit %d per request): use `ems open screens` on the Mac." % (rest, shots.MAX_REQUEST)
        return note
