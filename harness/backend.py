"""Start, stop and inspect the llama-server process for a profile."""

import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request

from . import config

STATE = config.RUN / "backend.json"     # what is running now
DESIRED = config.RUN / "desired.json"   # what the watchdog should keep running
OOM = re.compile(r"OutOfMemory|out of memory|failed to allocate|kIOGPUCommandBuffer", re.I)


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def state():
    return _read(STATE)


def desired():
    return _read(DESIRED)


def set_desired(profile, running):
    _write(DESIRED, {"profile": profile, "running": running, "at": time.time()})


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running():
    s = state()
    return s if alive(s.get("pid")) else None


def log_path(profile_name):
    return config.LOGS / ("backend-%s.log" % profile_name)


def health(port, timeout=3):
    """(http status or None, body dict)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (OSError, ValueError):
        return None, {}


def tail(path, lines=40):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * 1024))
            return f.read().decode("utf-8", "replace").splitlines()[-lines:]
    except OSError:
        return []


def start(profile, wait=True, timeout=900, log=print):
    current = running()
    if current:
        raise RuntimeError("backend already running (pid %d, profile %s)" % (current["pid"], current["profile"]))
    config.LOGS.mkdir(parents=True, exist_ok=True)
    logfile = log_path(profile.name)
    env = dict(os.environ, AIH_HOME=str(config.HOME))
    with open(logfile, "ab") as out:
        out.write(("\n===== start %s %s =====\n" % (profile.name, time.ctime())).encode())
        out.flush()
        proc = subprocess.Popen([str(config.INFERENCE / "serve.sh"), profile.name], stdout=out,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                start_new_session=True)
    _write(STATE, {"pid": proc.pid, "profile": profile.name, "port": profile.port,
                   "started": time.time(), "log": str(logfile)})
    set_desired(profile.name, True)
    log("started llama-server pid %d (profile %s), log %s" % (proc.pid, profile.name, logfile))
    if not wait:
        return proc.pid

    deadline = time.time() + timeout
    last = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited with code %s:\n%s"
                               % (proc.returncode, "\n".join(tail(logfile, 25))))
        code, _ = health(profile.port)
        if code == 200:
            log("ready after %.0f s on port %d" % (time.time() - (deadline - timeout), profile.port))
            return proc.pid
        if time.time() - last > 15:
            log("  loading... (%s)" % ("health %s" % code if code else "no answer yet"))
            last = time.time()
        time.sleep(2)
    raise RuntimeError("not ready after %d s; see %s" % (timeout, logfile))


def stop(timeout=30, log=print):
    s = state()
    set_desired(s.get("profile", config.DEFAULT_PROFILE), False)
    pid = s.get("pid")
    if not alive(pid):
        log("backend not running")
        return False
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    for _ in range(timeout * 2):
        if not alive(pid):
            break
        time.sleep(0.5)
    else:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    log("stopped pid %d" % pid)
    return True


def died_of_oom(profile_name):
    return any(OOM.search(line) for line in tail(log_path(profile_name), 80))


def recent_stats(profile_name):
    """Latest decode speed and MTP acceptance lines from the backend log."""
    speed = accept = None
    for line in tail(log_path(profile_name), 400):
        if "eval time" in line and "prompt" not in line:
            speed = line.strip()
        if "draft acceptance" in line:
            accept = line.strip()
    return speed, accept
