# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Builds one review page per group from the QA output: problems first, one picture per screen, key variants.

import glob
import html
import json
import os
import re

NAME_RE = re.compile(r"^(P\d{3})_S(\d{3})_(.+?)_(bn|en|ar)_(light|dark)\.png$")


def _latest_run(qa_out, phase):
    runs = [d for d in glob.glob(os.path.join(qa_out, "%s_*" % phase)) if os.path.isdir(d)]
    return max(runs, key=os.path.getmtime) if runs else None


def _problems(run):
    out = []
    rep = os.path.join(run, "REPORT.md")
    if os.path.exists(rep):
        for line in open(rep, encoding="utf-8", errors="replace"):
            if re.search(r"\b(FAIL|FAILED|ERROR|ISSUE|ISSUES)\b|✗|❌", line):
                out.append("REPORT: " + line.strip()[:240])
    con = os.path.join(run, "console.json")
    if os.path.exists(con):
        try:
            data = json.load(open(con, encoding="utf-8"))
        except ValueError:
            data = []
        items = data if isinstance(data, list) else data.get("messages", []) if isinstance(data, dict) else []
        errs = [i for i in items if isinstance(i, dict) and str(i.get("type") or i.get("level") or "").lower() in ("error", "pageerror")]
        for e in errs[:10]:
            out.append("Console: " + str(e.get("text") or e.get("message") or e)[:240])
        if len(errs) > 10:
            out.append("Console: … %d more errors" % (len(errs) - 10))
    return out


def collect_problems(project_root, phases):
    """Problem lines for the given phases (latest QA run of each), prefixed with the phase."""
    qa_out = os.path.join(project_root, "qa", "out")
    out = []
    for ph in phases:
        run = _latest_run(qa_out, ph)
        if not run:
            out.append("%s — no QA run yet" % ph)
            continue
        out += ["%s — %s" % (ph, x) for x in _problems(run)]
    return out


def build(project_root, phases, unit, key_variants=2):
    qa_out = os.path.join(project_root, "qa", "out")
    parts, problems_all, missing = [], [], []
    for ph in phases:
        run = _latest_run(qa_out, ph)
        if not run:
            missing.append(ph)
            continue
        shots = {}
        for p in sorted(glob.glob(os.path.join(run, "screenshots", "*.png")) + glob.glob(os.path.join(run, "*.png"))):
            m = NAME_RE.match(os.path.basename(p))
            if not m:
                continue
            _, step, rest, lang, theme = m.groups()
            screen = rest.split("_")[0]
            shots.setdefault(screen, []).append((step, rest, lang, theme, p))
        probs = _problems(run)
        problems_all += ["%s — %s" % (ph, x) for x in probs]
        rel = lambda p: os.path.relpath(p, qa_out)  # noqa: E731
        cards = []
        for screen, items in shots.items():
            bn = [i for i in items if i[2] == "bn" and i[3] == "light"] or items
            step, rest, lang, theme, p = bn[-1]
            cards.append('<figure><a href="%s"><img src="%s" loading="lazy"></a><figcaption>%s · S%s %s</figcaption></figure>'
                         % (html.escape(rel(p)), html.escape(rel(p)), html.escape(screen), step, html.escape(rest)))
        keys = sorted(shots.items(), key=lambda kv: -len({(i[2], i[3]) for i in kv[1]}))[:key_variants]
        var_html = []
        for screen, items in keys:
            combos = {}
            for step, rest, lang, theme, p in items:
                combos[(lang, theme)] = p
            if len(combos) < 2:
                continue
            row = "".join('<figure><a href="%s"><img src="%s" loading="lazy"></a><figcaption>%s %s</figcaption></figure>'
                          % (html.escape(rel(p)), html.escape(rel(p)), l, t) for (l, t), p in sorted(combos.items()))
            var_html.append("<h4>%s — languages and themes</h4><div class=grid6>%s</div>" % (html.escape(screen), row))
        total = sum(len(v) for v in shots.values())
        parts.append("<section><h2>%s <small>%s · %d screenshots in the folder, %d screens shown</small></h2>%s"
                     "<div class=grid>%s</div>%s</section>"
                     % (ph, html.escape(os.path.basename(run)), total, len(cards),
                        ("<ul class=bad>%s</ul>" % "".join("<li>%s</li>" % html.escape(x) for x in probs)) if probs else
                        "<p class=ok>No failures or console errors reported.</p>",
                        "".join(cards), "".join(var_html)))
    head = ("<!DOCTYPE html><html><head><meta charset=utf-8><title>Review %s</title><style>"
            "body{font:14px -apple-system,Segoe UI,sans-serif;margin:24px;color:#1d2939;background:#f9fafb}"
            "h1{margin:0 0 4px}h2{border-top:1px solid #e4e7ec;padding-top:16px}small{color:#667085;font-weight:400}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}"
            ".grid6{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}"
            "figure{margin:0;background:#fff;border:1px solid #e4e7ec;border-radius:8px;padding:6px}"
            "img{width:100%%;border-radius:4px}figcaption{font-size:12px;color:#475467;margin-top:4px}"
            ".bad{background:#fef3f2;border:1px solid #fecdca;padding:8px 24px;border-radius:8px}.ok{color:#067647}"
            "</style></head><body>" % html.escape(unit))
    summary = "<h1>Review %s</h1><p>Phases: %s. Problems first; one picture per screen (Bangla light); key screens in every language and theme. " \
              "Click a picture for full size. The full set stays in qa/out.</p>" % (html.escape(unit), ", ".join(phases))
    if missing:
        summary += "<p class=bad>No QA run yet for: %s</p>" % ", ".join(missing)
    if problems_all:
        summary += "<h2>Problems (%d)</h2><ul class=bad>%s</ul>" % (len(problems_all), "".join("<li>%s</li>" % html.escape(x) for x in problems_all))
    else:
        summary += "<p class=ok><b>No problems reported in any phase.</b></p>"
    out = os.path.join(qa_out, "review_%s.html" % unit)
    os.makedirs(qa_out, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(head + summary + "".join(parts) + "</body></html>")
    return out
