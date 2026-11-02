"""Operations that run on the inference host and print one JSON object.

harness-ui pipes this file to `python3 - <op> '<json args>'` inside the host's harness home (over SSH,
or locally), so nothing besides the synced harness/ package is needed there.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request

from harness import agent, backend, config, preflight, system

# Editable profile settings: key -> (type, constraint, label, help)
SETTINGS = {
    "CTX": ("int", [8192, 16384, 32768, 65536, 131072, 262144], "Context size (tokens)",
            "Maximum tokens per request. KV memory grows with it (~12 KB/token at q8_0)."),
    "PARALLEL": ("int", (1, 4), "Parallel requests",
                 "Requests served at once. They share the GPU, so each runs slower."),
    "MTP_ENABLED": ("bool", None, "MTP speculative decoding",
                    "Draft tokens with the MTP head (+3-8% measured). Costs ~4 GB of GPU memory."),
    "DRAFT_N_MAX": ("int", (1, 4), "MTP draft length",
                    "Tokens drafted per step. 1 measured best on the M1 Ultra; 2+ was slower."),
    "REASONING_EFFORT": ("enum", ["xhigh", "medium", "low", "off"], "Default thinking effort",
                         "Server-wide default for clients like Goose (Qwen3.8: low / medium / xhigh). "
                         "Requests can override it; the Chat tab has its own selector."),
    "REASONING_BUDGET": ("int", (-1, 65536), "Thinking token budget",
                         "Hard cap on thinking tokens per reply. -1 = unlimited, 0 = no thinking."),
    "CACHE_TYPE": ("enum", ["f16", "q8_0", "q4_0"], "KV cache type",
                   "Precision of the attention cache. q8_0 halves memory with negligible quality loss."),
    "CACHE_RAM_MB": ("int", (0, 16384), "Prompt cache RAM (MiB)",
                     "Host RAM for cached prompts of idle sessions. 0 disables. Too high causes swap."),
    "PORT": ("int", (1024, 65535), "Port", "HTTP port of the OpenAI-compatible API."),
    "HOST": ("enum", ["0.0.0.0", "127.0.0.1"], "Listen address",
             "0.0.0.0 serves the LAN; 127.0.0.1 only this machine."),
    "THREADS": ("int", (0, 32), "CPU threads", "0 = number of performance cores."),
    "API_KEY": ("secret", 128, "API key", "If set, clients must send 'Authorization: Bearer <key>'."),
    "GPU_BUDGET_GB": ("float", (1, 128), "GPU memory budget (GB)",
                      "Used by preflight to check the wired limit and free memory."),
    "EXTRA_ARGS": ("text", 500, "Extra llama-server arguments", "Appended verbatim to the command line."),
}
DEFAULTS = {"HOST": "0.0.0.0", "THREADS": "0", "API_KEY": "", "EXTRA_ARGS": "", "CACHE_RAM_MB": "2048",
            "DRAFT_N_MAX": "1", "PARALLEL": "1", "CACHE_TYPE": "f16",
            "REASONING_EFFORT": "xhigh", "REASONING_BUDGET": "-1"}

_NUM = r"([\d.]+)"


def _http_json(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except Exception:
        return None


def _session_stats(profile_name):
    """Aggregate request timings since the last server start from the backend log."""
    path = backend.log_path(profile_name)
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    starts = [i for i, l in enumerate(lines) if l.startswith("==> ")]
    lines = lines[starts[-1]:] if starts else lines
    req = gen = gen_ms = acc = drafted = 0
    last = {}
    for l in lines:
        m = re.search(r"\|\s+eval time =\s+" + _NUM + r" ms /\s+(\d+) tokens.*?" + _NUM + " tokens per second", l)
        if m and "prompt" not in l:
            req += 1
            gen_ms += float(m.group(1))
            gen += int(m.group(2))
            last["decode_tps"] = float(m.group(3))
            last["tokens"] = int(m.group(2))
        m = re.search(r"prompt eval time =\s+" + _NUM + r" ms /\s+(\d+) tokens.*?" + _NUM + " tokens per second", l)
        if m:
            last["prompt_tokens"] = int(m.group(2))
            last["prefill_tps"] = float(m.group(3))
        m = re.search(r"draft acceptance = [\d.]+ \(\s*(\d+) accepted /\s*(\d+) generated", l)
        if m:
            acc += int(m.group(1))
            drafted += int(m.group(2))
            last["accept"] = int(m.group(1)) / max(1, int(m.group(2)))
    return {
        "requests": req,
        "generated": gen,
        "decode_tps": gen / gen_ms * 1000 if gen_ms else None,
        "accept": acc / drafted if drafted else None,
        "last": last,
    }


def _running_flags(pid):
    try:
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return {}
    flags = {}
    for key, pat in (("ctx", r" -c (\d+)"), ("parallel", r"--parallel (\d+)"), ("cache_ram", r"--cache-ram (-?\d+)"),
                     ("draft_n_max", r"--spec-draft-n-max (\d+)"), ("cache_type", r"-ctk (\S+)"),
                     ("port", r"--port (\d+)"), ("reasoning_budget", r"--reasoning-budget (-?\d+)"),
                     ("effort", r'"reasoning_effort":"(\w+)"')):
        m = re.search(pat, args)
        if m:
            flags[key] = m.group(1)
    flags["mtp"] = "draft-mtp" in args
    flags["auth"] = "--api-key" in args
    if '"enable_thinking":false' in args:
        flags["effort"] = "off"
    return flags


def op_status(_):
    s = backend.running()
    out = {"time": time.time(), "backend": None, "desired": backend.desired()}
    if s:
        code, body = backend.health(s["port"])
        slots = _http_json("http://127.0.0.1:%d/slots" % s["port"]) or []
        out["backend"] = {
            "pid": s["pid"], "profile": s["profile"], "port": s["port"], "started": s["started"],
            "uptime_s": time.time() - s["started"], "health": body.get("status") or code,
            "busy_slots": sum(1 for x in slots if isinstance(x, dict) and x.get("is_processing")),
            "slots": len(slots), "flags": _running_flags(s["pid"]),
            "stats": _session_stats(s["profile"]),
        }
        ov = config.overrides_path(s["profile"])
        out["backend"]["settings_changed"] = ov.exists() and ov.stat().st_mtime > s["started"]
    vm = system.vm_stats_gb()
    out["memory"] = {"total": system.mem_total_gb(), "wired": vm["wired"], "active": vm["active"],
                     "compressed": vm["compressed"], "available": vm["free"] + vm["reclaimable"],
                     "swap_mb": system.swap_used_mb(), "wired_limit_mb": system.wired_limit_mb()}
    hb = backend._read(config.RUN / "watchdog.json").get("heartbeat")
    out["watchdog"] = {"installed": agent.loaded(), "heartbeat_age": time.time() - hb if hb else None}
    out["build"] = {"llama_server": config.LLAMA_SERVER.exists()}
    return out


def _profile_view(name):
    base = config.load_profile(name, overrides=False)
    eff = config.load_profile(name)
    ov_path = config.overrides_path(name)
    overrides = {}
    if ov_path.exists():
        tmp = {}
        config._read_env(ov_path, tmp)
        overrides = tmp
    def val(p, key):
        if key == "MTP_ENABLED":
            return bool(p.get("MTP_FILE"))
        return p.get(key, DEFAULTS.get(key, ""))
    settings = {}
    for key, (typ, cons, label, help_text) in SETTINGS.items():
        settings[key] = {
            "type": typ, "label": label, "help": help_text,
            "choices": cons if isinstance(cons, list) else None,
            "range": list(cons) if isinstance(cons, tuple) else None,
            "value": val(eff, key), "default": val(base, key),
            "overridden": (key == "MTP_ENABLED" and "MTP_FILE" in overrides) or key in overrides,
        }
        if typ == "secret":
            settings[key]["value"] = "••••••" if eff.get(key) else ""
            settings[key]["is_set"] = bool(eff.get(key))
    return {"name": name, "description": eff.get("DESCRIPTION", ""), "alias": eff.get("ALIAS"),
            "model": eff.get("MODEL_FILE"), "mtp_available": bool(base.get("MTP_FILE")),
            "settings": settings}


def op_profiles(_):
    return {"profiles": [_profile_view(n) for n in config.list_profiles()]}


def _validate(key, value):
    typ, cons, label, _ = SETTINGS[key]
    if typ == "int":
        v = int(value)
        if isinstance(cons, list) and v not in cons:
            raise ValueError("%s must be one of %s" % (label, cons))
        if isinstance(cons, tuple) and not cons[0] <= v <= cons[1]:
            raise ValueError("%s must be between %d and %d" % (label, cons[0], cons[1]))
        return str(v)
    if typ == "float":
        v = float(value)
        if not cons[0] <= v <= cons[1]:
            raise ValueError("%s must be between %s and %s" % (label, cons[0], cons[1]))
        return ("%g" % v)
    if typ == "enum":
        if value not in cons:
            raise ValueError("%s must be one of %s" % (label, cons))
        return value
    if typ in ("text", "secret"):
        value = str(value)
        if "\n" in value or "\r" in value or len(value) > cons:
            raise ValueError("%s must be one line, at most %d characters" % (label, cons))
        if typ == "secret" and re.search(r"\s", value):
            raise ValueError("%s must not contain spaces" % label)
        return value
    if typ == "bool":
        return bool(value)
    raise ValueError("unknown setting " + key)


def op_save_settings(args):
    name, values = args["profile"], args["values"]
    base = config.load_profile(name, overrides=False)
    ov_path = config.overrides_path(name)
    current = {}
    if ov_path.exists():
        config._read_env(ov_path, current)
    for key, value in values.items():
        if key not in SETTINGS:
            raise ValueError("unknown setting " + key)
        if SETTINGS[key][0] == "secret" and value == "••••••":
            continue  # unchanged placeholder
        v = _validate(key, value)
        if key == "MTP_ENABLED":
            if v and not base.get("MTP_FILE"):
                raise ValueError("this profile has no MTP head")
            if v:
                current.pop("MTP_FILE", None)
            else:
                current["MTP_FILE"] = ""
            continue
        if v == base.get(key, DEFAULTS.get(key, "")):
            current.pop(key, None)
        else:
            current[key] = v
    if current:
        body = "# Per-machine overrides for profile %r, written by harness-ui. Delete to reset.\n" % name
        body += "".join("%s=%s\n" % (k, shlex.quote(v)) for k, v in sorted(current.items()))
        ov_path.write_text(body)
        os.chmod(str(ov_path), 0o600)  # may hold an API key
    elif ov_path.exists():
        ov_path.unlink()
    return _profile_view(name)


def op_reset_settings(args):
    p = config.overrides_path(args["profile"])
    if p.exists():
        p.unlink()
    return _profile_view(args["profile"])


def op_preflight(args):
    profile = config.load_profile(args["profile"])
    results = preflight.run(profile, backend_running=bool(backend.running()))
    return {"ok": preflight.ok(results), "results": [list(r) for r in results]}


def op_start(args):
    profile = config.load_profile(args["profile"])
    if not args.get("force"):
        results = preflight.run(profile, backend_running=bool(backend.running()))
        if not preflight.ok(results):
            return {"started": False, "preflight": [list(r) for r in results]}
    pid = backend.start(profile, wait=False, log=lambda *_: None)
    return {"started": True, "pid": pid, "port": profile.port}


def op_stop(_):
    return {"stopped": backend.stop(log=lambda *_: None)}


def op_logs(args):
    n = int(args.get("lines", 200))
    if args.get("watchdog"):
        path = config.LOGS / "watchdog.log"
    else:
        name = args.get("profile") or backend.state().get("profile", config.DEFAULT_PROFILE)
        path = backend.log_path(name)
    return {"path": str(path), "lines": backend.tail(path, n)}


def op_agent(args):
    if args["action"] == "install":
        agent.install()
    else:
        agent.uninstall()
    return {"installed": agent.loaded()}


def op_api_key(args):
    """Effective API key of a profile, for the UI server's proxy only (never sent to the browser)."""
    return {"api_key": config.load_profile(args["profile"]).get("API_KEY", "")}


def main():
    op = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    fn = globals().get("op_" + op)
    try:
        if fn is None:
            raise ValueError("unknown op " + op)
        result = {"ok": True, "data": fn(args)}
    except SystemExit as e:
        result = {"ok": False, "error": str(e)}
    except Exception as e:  # report every failure as JSON so the UI can show it
        result = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    sys.stdout.write("\n@@AIH_JSON@@" + json.dumps(result))


main()
