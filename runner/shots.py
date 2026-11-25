# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Finds QA screenshots of a phase and plans how to send them to Telegram (albums of 10, at most 30 per request, skip files over 10 MB).

import glob
import os

from .review import NAME_RE, _latest_run

MAX_ALBUM = 10
MAX_REQUEST = 30
MAX_BYTES = 10 * 1024 * 1024


def latest_run(project_root, phase):
    return _latest_run(os.path.join(project_root, "qa", "out"), phase)


def list_shots(run):
    """All screenshots of one QA run as dicts, sorted by step."""
    out = []
    if not run:
        return out
    for p in sorted(glob.glob(os.path.join(run, "screenshots", "*.png")) + glob.glob(os.path.join(run, "*.png"))):
        m = NAME_RE.match(os.path.basename(p))
        if m:
            ph, step, rest, lang, theme = m.groups()
            out.append({"path": p, "phase": ph, "step": step, "rest": rest, "screen": rest.split("_")[0],
                        "lang": lang, "theme": theme})
    return out


def every_step_bn_light(shots):
    return [s for s in shots if s["lang"] == "bn" and s["theme"] == "light"]


def key_screens(shots, n=5):
    """The n screens with the most language/theme variants (same idea as the review page)."""
    variants = {}
    for s in shots:
        variants.setdefault(s["screen"], set()).add((s["lang"], s["theme"]))
    return [k for k, _ in sorted(variants.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:n]]


def key_bn_light(shots, n=5):
    keys = key_screens(shots, n)
    out = []
    for k in keys:
        items = [s for s in shots if s["screen"] == k and s["lang"] == "bn" and s["theme"] == "light"] or \
                [s for s in shots if s["screen"] == k]
        if items:
            out.append(items[-1])
    return out


def key_all6(shots, n=5):
    keys = key_screens(shots, n)
    out = []
    for k in keys:
        combos = {}
        for s in shots:
            if s["screen"] == k:
                combos[(s["lang"], s["theme"])] = s  # latest step wins
        out += [combos[c] for c in sorted(combos)]
    return out


def caption(s):
    return ("%s S%s %s %s %s" % (s["phase"], s["step"], s["rest"], s["lang"], s["theme"]))[:200]


def plan_albums(shots, max_album=MAX_ALBUM, max_total=MAX_REQUEST, max_bytes=MAX_BYTES):
    """-> (albums, skipped_big, not_sent). albums = lists of at most max_album shots, at most max_total shots in all."""
    ok, big = [], []
    for s in shots:
        try:
            size = os.path.getsize(s["path"])
        except OSError:
            continue
        (big if size > max_bytes else ok).append(s)
    send, rest = ok[:max_total], ok[max_total:]
    return [send[i:i + max_album] for i in range(0, len(send), max_album)], big, len(rest)
