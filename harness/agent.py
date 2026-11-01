"""Install or remove the watchdog as a user LaunchAgent (no sudo needed)."""

import os
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "ai.harness.watchdog"
TEMPLATE = Path(__file__).parent / "launchd" / (LABEL + ".plist")
TARGET = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def _domain():
    return "gui/%d" % os.getuid()


def install():
    config.LOGS.mkdir(parents=True, exist_ok=True)
    text = TEMPLATE.read_text().replace("@HOME@", str(config.HOME)).replace("@PYTHON@", sys.executable)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", _domain(), str(TARGET)], capture_output=True)
    TARGET.write_text(text)
    r = subprocess.run(["launchctl", "bootstrap", _domain(), str(TARGET)], capture_output=True, text=True)
    if r.returncode != 0:
        # An SSH session may have no GUI domain; fall back to the user domain.
        r = subprocess.run(["launchctl", "bootstrap", "user/%d" % os.getuid(), str(TARGET)],
                           capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("launchctl bootstrap failed: %s" % (r.stderr.strip() or r.stdout.strip()))
    print("installed %s -> %s" % (LABEL, TARGET))


def uninstall():
    for domain in (_domain(), "user/%d" % os.getuid()):
        subprocess.run(["launchctl", "bootout", domain, str(TARGET)], capture_output=True)
    if TARGET.exists():
        TARGET.unlink()
    print("removed %s" % LABEL)


def loaded():
    r = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    return r.returncode == 0
