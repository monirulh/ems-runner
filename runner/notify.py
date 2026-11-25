# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Sends runner notifications to macOS, a Telegram bot and ntfy; failures are logged, never fatal.

import json
import shutil
import subprocess
import urllib.parse
import urllib.request

from .common import load_secrets

LEVELS = {"info": 1, "urgent": 2}


def _min_level(cfg, channel, default):
    return LEVELS.get(cfg.get("notify", channel + "_min_level", default), LEVELS[default])


def _esc_applescript(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send(cfg, log, level, title, body=""):
    """level: 'info' or 'urgent'. Returns list of channels that succeeded."""
    lvl = LEVELS.get(level, 1)
    ok = []
    if cfg.getbool("notify", "dry_run", False):
        log("notify(dry-run) [%s] %s: %s" % (level, title, body), kind="notify", level=level, title=title)
        return ["dry-run"]
    secrets = load_secrets()

    if cfg.getbool("notify", "macos", True) and lvl >= _min_level(cfg, "macos", "info") and shutil.which("osascript"):
        script = 'display notification "%s" with title "EMS runner" subtitle "%s"%s' % (
            _esc_applescript(body[:200]), _esc_applescript(title[:100]),
            ' sound name "Glass"' if (level == "urgent" and cfg.getbool("notify", "macos_sound", False)) else "")
        try:
            subprocess.run(["osascript", "-e", script], timeout=10, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ok.append("macos")
        except Exception as e:  # noqa: BLE001
            log("notify macos failed: %s" % e)

    token, chat = secrets.get("TELEGRAM_BOT_TOKEN"), secrets.get("TELEGRAM_CHAT_ID")
    if cfg.getbool("notify", "telegram", True) and token and chat and lvl >= _min_level(cfg, "telegram", "info"):
        text = ("🔴 " if level == "urgent" else "ℹ️ ") + title + ("\n" + body if body else "")
        fields = {"chat_id": chat, "text": text[:3900]}
        if level != "urgent" and cfg.getbool("notify", "telegram_silent_info", True):
            fields["disable_notification"] = "true"  # info arrives without a sound
        data = urllib.parse.urlencode(fields).encode()
        try:
            urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % token, data=data, timeout=15).read()
            ok.append("telegram")
        except Exception as e:  # noqa: BLE001
            log("notify telegram failed: %s" % type(e).__name__)  # never log the token

    topic = secrets.get("NTFY_TOPIC")
    if cfg.getbool("notify", "ntfy", True) and topic and lvl >= _min_level(cfg, "ntfy", "info"):
        server = secrets.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        req = urllib.request.Request(server + "/" + topic, data=(body or title).encode("utf-8"), method="POST")
        req.add_header("Title", title.encode("ascii", "replace").decode())
        req.add_header("Priority", "high" if level == "urgent" else "default")
        req.add_header("Tags", "rotating_light" if level == "urgent" else "information_source")
        try:
            urllib.request.urlopen(req, timeout=15).read()
            ok.append("ntfy")
        except Exception as e:  # noqa: BLE001
            log("notify ntfy failed: %s" % type(e).__name__)

    log("notified [%s] %s — %s via %s" % (level, title, body, ",".join(ok) or "none"),
        kind="notify", level=level, title=title, channels=ok)
    return ok


def telegram_find_chat_id(token):
    """Helper for setup: returns chat ids that messaged the bot."""
    raw = urllib.request.urlopen("https://api.telegram.org/bot%s/getUpdates" % token, timeout=15).read()
    data = json.loads(raw)
    ids = []
    for upd in data.get("result", []):
        chat = (upd.get("message") or {}).get("chat") or {}
        if chat.get("id") and chat["id"] not in ids:
            ids.append(chat["id"])
    return ids
