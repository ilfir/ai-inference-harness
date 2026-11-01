"""Keeps the backend in its desired state. Runs under launchd (see harness/launchd/)."""

import collections
import time

from . import backend, config, system

RESTART_WINDOW_S = 600
MAX_RESTARTS = 3
SWAP_ALERT_MB = 512


def _log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def run(interval=5):
    restarts = collections.deque()
    fell_back = set()
    swap_mark = (time.time(), system.swap_used_mb())
    _log("watchdog started (home %s)" % config.HOME)
    while True:
        want = backend.desired()
        if want.get("running") and not backend.running():
            name = want.get("profile", config.DEFAULT_PROFILE)
            now = time.time()
            while restarts and now - restarts[0] > RESTART_WINDOW_S:
                restarts.popleft()
            if len(restarts) >= MAX_RESTARTS:
                _log("backend keeps dying; giving up until the next `harness serve`")
                backend.set_desired(name, False)
            else:
                profile = config.load_profile(name)
                fallback = profile.get("FALLBACK_PROFILE")
                if backend.died_of_oom(name) and fallback and name not in fell_back:
                    _log("Metal OOM in profile %s; falling back to %s" % (name, fallback))
                    fell_back.add(name)
                    profile = config.load_profile(fallback)
                _log("starting backend (profile %s)" % profile.name)
                restarts.append(now)
                try:
                    backend.start(profile, wait=False, log=_log)
                except Exception as e:  # keep the watchdog alive whatever happens
                    _log("start failed: %s" % e)

        t0, swap0 = swap_mark
        if time.time() - t0 >= 60:
            swap = system.swap_used_mb()
            if backend.running() and swap - swap0 > SWAP_ALERT_MB:
                _log("WARNING swap grew %.0f MB in a minute while serving; memory budget is too tight"
                     % (swap - swap0))
            swap_mark = (time.time(), swap)

        backend._write(config.RUN / "watchdog.json", {"heartbeat": time.time()})
        time.sleep(interval)
