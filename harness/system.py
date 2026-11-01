"""macOS system probes: memory, wired GPU limit, swap, large processes, disk."""

import re
import shutil
import subprocess


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def sysctl(name):
    return _run(["sysctl", "-n", name]).strip()


def mem_total_gb():
    value = sysctl("hw.memsize")
    return int(value) / 2**30 if value else 0.0


def wired_limit_mb():
    """iogpu.wired_limit_mb; 0 means the macOS default (about 75% of RAM on 64 GB machines)."""
    value = sysctl("iogpu.wired_limit_mb")
    return int(value) if value.isdigit() else 0


def vm_stats_gb():
    out = _run(["vm_stat"])
    m = re.search(r"page size of (\d+) bytes", out)
    page = int(m.group(1)) if m else 16384
    pages = {}
    for line in out.splitlines()[1:]:
        key, _, value = line.partition(":")
        value = value.strip().rstrip(".")
        if value.isdigit():
            pages[key.strip()] = int(value)

    def gb(*keys):
        return sum(pages.get(k, 0) for k in keys) * page / 2**30

    return {
        "free": gb("Pages free", "Pages speculative"),
        "reclaimable": gb("Pages inactive", "Pages purgeable"),
        "wired": gb("Pages wired down"),
        "active": gb("Pages active"),
        "compressed": gb("Pages occupied by compressor"),
    }


def available_gb():
    s = vm_stats_gb()
    return s["free"] + s["reclaimable"]


def swap_used_mb():
    m = re.search(r"used = ([\d.]+)M", sysctl("vm.swapusage"))
    return float(m.group(1)) if m else 0.0


def big_processes(min_gb=2.0):
    """[(pid, rss_gb, command)] sorted by RSS, for processes above min_gb."""
    rows = []
    for line in _run(["ps", "-axo", "pid=,rss=,comm="]).splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1].isdigit():
            rss_gb = int(parts[1]) / 2**20
            if rss_gb >= min_gb:
                rows.append((int(parts[0]), rss_gb, parts[2]))
    return sorted(rows, key=lambda r: -r[1])


def disk_free_gb(path):
    return shutil.disk_usage(str(path)).free / 2**30


def port_in_use(port):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False
