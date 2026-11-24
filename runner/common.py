# Copyright (c) 2026 Monirul Hasan. All rights reserved.
# Shared helpers for the EMS runner: config, paths, locked JSON state, logging and time.

import configparser
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import time

RUNNER_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def expand(path):
    return os.path.abspath(os.path.expanduser(path))


def now():
    return time.time()


def iso(ts=None):
    if ts is None:
        ts = now()
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def hm(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%a %H:%M")


def fmt_dur(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return "%dh%02dm" % (h, m) if h else "%dm" % m


def parse_duration(text):
    """'5h', '90m', '1h30m', '2.5h' -> seconds."""
    text = text.strip().lower()
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+)m)?", text)
    if not m or not (m.group(1) or m.group(2)):
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return int(float(text) * 3600)
        raise ValueError("duration like 5h, 90m or 1h30m expected, got %r" % text)
    hours = float(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    return int(hours * 3600 + mins * 60)


def parse_until(text, ref=None):
    """'06:00' -> next timestamp at that local time."""
    ref = ref or now()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text.strip())
    if not m:
        raise ValueError("time like 06:00 expected")
    base = dt.datetime.fromtimestamp(ref)
    target = base.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if target.timestamp() <= ref:
        target += dt.timedelta(days=1)
    return target.timestamp()


class Config:
    """config.ini in the runner folder, overridden by the local file outside the repo."""

    def __init__(self):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(os.path.join(RUNNER_HOME, "config.ini"))
        local = os.environ.get("EMS_RUNNER_LOCAL", "~/.skysoft/ems-runner.ini")
        cp.read(expand(local))
        self.cp = cp

    def get(self, sec, key, default=None):
        try:
            val = self.cp.get(sec, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default
        return val.strip()

    def getint(self, sec, key, default=0):
        val = self.get(sec, key)
        return int(val) if val not in (None, "") else default

    def getfloat(self, sec, key, default=0.0):
        val = self.get(sec, key)
        return float(val) if val not in (None, "") else default

    def getbool(self, sec, key, default=False):
        val = self.get(sec, key)
        if val in (None, ""):
            return default
        return val.lower() in ("1", "yes", "true", "on")

    def getlist(self, sec, key, default=None):
        val = self.get(sec, key)
        if val in (None, ""):
            return list(default or [])
        return [x.strip() for x in re.split(r"[,\n]", val) if x.strip()]

    def section(self, sec):
        return dict(self.cp.items(sec)) if self.cp.has_section(sec) else {}

    @property
    def project_root(self):
        return expand(os.environ.get("EMS_PROJECT_ROOT") or self.get("paths", "project_root") or os.path.join(RUNNER_HOME, "..", ".."))

    @property
    def var_dir(self):
        return expand(os.environ.get("EMS_RUNNER_VAR") or self.get("paths", "var_dir") or os.path.join(RUNNER_HOME, "var"))

    @property
    def agent_cli(self):
        return os.environ.get("EMS_AGENT_CLI") or self.get("agent", "cli_path")

    def model_for(self, kind):
        return self.get("models", kind) or self.get("models", "default", "sonnet")

    def max_turns_for(self, kind):
        return self.getint("max_turns", kind, self.getint("max_turns", "default", 80))


class Store:
    """JSON files in var/, read-modify-write under one file lock (CLI and daemon share it)."""

    def __init__(self, var_dir):
        self.var = var_dir
        os.makedirs(os.path.join(var_dir, "logs", "runs"), exist_ok=True)
        os.makedirs(os.path.join(var_dir, "plans"), exist_ok=True)
        self.lock_path = os.path.join(var_dir, "store.lock")
        self._rlock = threading.RLock()
        self._depth = 0

    def path(self, name):
        return os.path.join(self.var, name)

    @contextlib.contextmanager
    def locked(self):
        """Re-entrant within one process (thread-safe), exclusive across processes."""
        with self._rlock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._depth = 1
            try:
                yield
            finally:
                self._depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def load(self, name, default):
        p = self.path(name)
        if not os.path.exists(p):
            return default
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, name, data):
        write_json(self.path(name), data)


def write_json(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


DEFAULT_STATE = {
    "mode": "asleep",          # asleep | running | paused | waiting
    "pause_reason": None,
    "session": None,           # {"start": ts, "end": ts}
    "wait": None,              # {"reason": limit|network|service, "until": ts, "detail": str}
    "current": None,           # {"task": id, "model": str, "started": ts, "pid": int}
    "interrupt": None,         # reason string set by the CLI
    "model_blocks": {},        # model -> ts until which it is limited
    "service_failures": 0,
    "last_checkpoint": None,
    "next_action": None,
    "baseline": None,          # {"master_sha": str, "file": str}
    "seen": {"to-dev": [], "to-skyui": [], "to-research": []},
    "notified": [],
}


class Logger:
    def __init__(self, var_dir):
        self.human = os.path.join(var_dir, "logs", "runner.log")
        self.events = os.path.join(var_dir, "logs", "events.jsonl")

    def __call__(self, msg, **fields):
        line = "%s  %s" % (iso(), msg)
        with open(self.human, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        rec = {"ts": now(), "msg": msg}
        rec.update(fields)
        with open(self.events, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if os.environ.get("EMS_RUNNER_ECHO"):
            print(line, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def load_secrets(path="~/.skysoft/secrets.env"):
    out = {}
    for line in read_text(expand(os.environ.get("EMS_SECRETS_FILE", path))).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out
