"""Run harness commands on the inference host over SSH.

The repo's inference/, harness/ and client/ folders are rsynced to the host first, so the host
always runs the same code as this checkout. Nothing listens besides llama-server itself.
"""

import shlex
import subprocess
import sys

from . import config

SYNC_DIRS = ("inference", "harness", "client")


def http_host(host):
    return host.rsplit("@", 1)[-1]


def sync(host, remote_home):
    subprocess.run(["ssh", host, "mkdir -p %s" % remote_home], check=True)
    cmd = ["rsync", "-az", "--delete", "--exclude", "__pycache__", "--exclude", ".DS_Store"]
    cmd += [str(config.REPO / d) for d in SYNC_DIRS]
    cmd.append("%s:%s/" % (host, remote_home))
    subprocess.run(cmd, check=True)


def run(host, remote_home, argv, tty=False):
    """Run `python3 -m harness <argv>` on the host, streaming output. Returns the exit code."""
    home = remote_home.replace("~", "$HOME", 1) if remote_home.startswith("~") else remote_home
    inner = 'cd "%s" && AIH_HOME="%s" /usr/bin/python3 -m harness %s' % (
        home, home, " ".join(shlex.quote(a) for a in argv))
    ssh = ["ssh"] + (["-t"] if tty and sys.stdout.isatty() else []) + [host, inner]
    return subprocess.call(ssh)
