"""Operations that run on the inference host and print one JSON object.

harness-ui pipes this file to `python3 - <op> '<json args>'` inside the host's harness home (over SSH,
or locally), so nothing besides the synced harness/ package is needed there.

Two engines are known. llama.cpp is the harness's own backend (harness.backend, inference/profiles). Strata is
the engine prepared by the ai-inference-strata trial in AIH_STRATA_HOME (default ~/ai-inference-strata): it is
started and stopped only through that directory's scripts/strata-start.sh and strata-stop.sh, with the profile's
environment knobs sent by the app. Only one engine can hold the GPU at a time.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

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

# ---------------------------------------------------------------- Strata (ai-inference-strata trial directory)
STRATA_HOME = Path(os.path.expanduser(os.environ.get("AIH_STRATA_HOME") or "~/ai-inference-strata"))
STRATA_STATE = config.RUN / "strata.json"       # which Strata profile the app started (Strata keeps its own pid file)
STRATA_START_LOG = config.LOGS / "strata-start.log"
STRATA_WIRED_FREE_GB = 20                       # strata-start.sh refuses while more than this is wired
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _sp():
    """Paths inside the Strata directory that the app relies on (all created by the trial's setup)."""
    h = STRATA_HOME
    return {
        "start": h / "scripts" / "strata-start.sh", "stop": h / "scripts" / "strata-stop.sh",
        "make_config": h / "scripts" / "make-config.py", "venv_py": h / "Strata" / ".venv" / "bin" / "python",
        "pid": h / "run" / "strata-server.pid", "config": h / "config" / "strata-iq3xxs.json",
        "engine": h / "Strata" / "build-metal" / "strata", "server_py": h / "Strata" / "serve" / "server.py",
        "pack": h / "Strata-data" / "packs" / "iq3_xxs" / "native_experts.txt",
        "mtp": h / "Strata-data" / "mtp" / "mtp-q2_0.gguf", "mtp_rt": h / "Strata-data" / "mtp" / "rt",
        "server_log": h / "logs" / "strata-server-latest.log", "engine_log": h / "logs" / "strata-engine.log",
    }


def _http_json(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except Exception:
        return None


def _strata_config():
    try:
        return json.loads(_sp()["config"].read_text())
    except (OSError, ValueError):
        return {}


def _strata_running():
    """{pid, port, started, profile, cmd} when the Strata server of STRATA_HOME is alive, else None."""
    p = _sp()
    try:
        pid = int(p["pid"].read_text().strip())
    except (OSError, ValueError):
        return None
    if not backend.alive(pid):
        return None
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    except OSError:
        cmd = ""
    if "serve/server.py" not in cmd:
        return None  # stale pid file now pointing at some other process
    cfg = _strata_config()
    st = backend._read(STRATA_STATE)
    try:
        started = p["pid"].stat().st_mtime
    except OSError:
        started = time.time()
    return {"pid": pid, "port": int(cfg.get("port") or 8081), "started": started, "cmd": cmd,
            "profile": st.get("profile") if st.get("pid") == pid else "external"}


def _strata_flags(cfg):
    """The running configuration from Strata's run config (args in the engine's names)."""
    args = list(cfg.get("args") or [])

    def after(flag):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None

    stream = (cfg.get("env") or {}).get("STRATA_METAL_STREAM")
    return {
        "ctx": after("--max-context"), "parallel": str(cfg.get("parallel") or 1), "port": str(cfg.get("port") or ""),
        "mtp": "--mtp" in args, "spec": after("--spec"), "suffix_draft": after("--suffix-draft") or "default",
        "kv": after("--kv") or "fp16", "conv_cache": after("--conversation-cache-mib"),
        "weights": {"0": "resident", "1": "streaming"}.get(stream, "auto"),
        "auth": bool(cfg.get("api_key")), "host": cfg.get("host"),
    }


def _strata_status(s):
    base = "http://127.0.0.1:%d" % s["port"]
    h = _http_json(base + "/health")
    health = ("ok" if h.get("loaded") else "loading") if isinstance(h, dict) else None
    slots = _http_json(base + "/slots") or []
    m = _http_json(base + "/metrics", timeout=3) or {}
    totals, eng = m.get("totals") or {}, m.get("engine") or {}
    stats = None
    if totals:
        out_tok, dec_ms = totals.get("output_tokens") or 0, totals.get("decode_ms") or 0
        offered, accepted = totals.get("drafts_offered") or 0, totals.get("drafts_accepted") or 0
        last = {}
        for r in m.get("requests") or []:      # newest first: raw per-request fields (not llama's timings names)
            if isinstance(r, dict) and r.get("output_tokens"):
                new_tok = max(0, int(r.get("prompt_tokens") or 0) - int(r.get("reused") or 0))
                pm = r.get("prompt_ms") or 0
                last = {"tokens": r.get("output_tokens"), "decode_tps": r.get("decode_tok_s"),
                        "prompt_tokens": new_tok, "prefill_tps": new_tok / pm * 1000 if new_tok and pm else None,
                        "accept": (r["drafts_accepted"] / r["drafts_offered"]) if r.get("drafts_offered") else None}
                break
        stats = {"requests": totals.get("requests"), "generated": out_tok,
                 "decode_tps": out_tok / dec_ms * 1000 if dec_ms else None,
                 "accept": accepted / offered if offered else None, "last": last}
    return {
        "engine": "strata", "version": eng.get("version"), "pid": s["pid"], "profile": s["profile"],
        "port": s["port"], "started": s["started"], "uptime_s": time.time() - s["started"], "health": health,
        "busy_slots": sum(1 for x in slots if isinstance(x, dict) and x.get("is_processing")),
        "slots": len(slots), "flags": _strata_flags(_strata_config()), "stats": stats, "settings_changed": False,
        "drafting": eng.get("drafting"),
    }


def _strata_preflight(name, env, backend_running):
    p = _sp()
    r = []
    if not STRATA_HOME.is_dir():
        return [(preflight.FAIL, "Strata home", "%s not found; set it in Settings → Connection" % STRATA_HOME)]
    r.append((preflight.PASS, "Strata home", str(STRATA_HOME)))
    for key, label, hint in (("start", "start script", "scripts/strata-start.sh"), ("engine", "engine binary", "Strata/build-metal/strata"),
                             ("server_py", "Strata server", "Strata/serve/server.py"), ("pack", "model pack", "Strata-data/packs/iq3_xxs")):
        r.append((preflight.PASS if p[key].exists() else preflight.FAIL, label,
                  str(p[key]) if p[key].exists() else "%s missing (%s)" % (hint, p[key])))
    if env.get("MTP") == "1":
        if p["mtp"].exists() and p["mtp_rt"].is_dir():
            r.append((preflight.PASS, "MTP layer", "%s (%.2f GB)" % (p["mtp"].name, p["mtp"].stat().st_size / 1e9)))
        else:
            r.append((preflight.FAIL, "MTP layer", "%s missing; run scripts/prepare.sh --download-mtp or turn MTP off" % p["mtp"]))
    else:
        r.append((preflight.PASS, "MTP layer", "not used by this profile"))
    if p["make_config"].exists() and p["venv_py"].exists():
        # The trial's make-config.py validates every file the engine needs and shows the exact arguments. It is
        # written to a scratch file so a running Strata's own config (which the Dashboard reads) stays untouched.
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="aih-strata-")) / "dry-run.json"
        try:
            d = subprocess.run([str(p["venv_py"]), str(p["make_config"]), str(tmp)], capture_output=True, text=True,
                               timeout=60, env=_strata_env(env, force=False), cwd=str(STRATA_HOME))
            if d.returncode == 0:
                cfg = json.loads(tmp.read_text())
                r.append((preflight.PASS, "engine config", " ".join(cfg.get("args") or [])[-220:]))
            else:
                r.append((preflight.FAIL, "engine config", (d.stderr or d.stdout).strip()[-300:] or "exit %d" % d.returncode))
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            r.append((preflight.FAIL, "engine config", str(e)))
        finally:
            try:
                tmp.unlink()
                tmp.parent.rmdir()
            except OSError:
                pass
    else:
        r.append((preflight.FAIL, "engine config", "make-config.py or Strata/.venv missing under %s" % STRATA_HOME))
    llama = backend.running()
    if llama:
        r.append((preflight.WARN, "llama.cpp backend", "running (pid %d, profile %s); Restart stops it first" % (llama["pid"], llama["profile"])))
    if agent.loaded() and backend.desired().get("running"):
        r.append((preflight.WARN, "harness watchdog", "installed and set to keep llama.cpp running; starting Strata clears that"))
    budget = config.Profile({"NAME": name, "GPU_BUDGET_GB": env.get("GPU_BUDGET_GB", "57")})
    r += preflight._memory(budget, backend_running)
    port = int(env.get("PORT") or 8081)
    if system.port_in_use(port) and not backend_running:
        r.append((preflight.FAIL, "port", "port %d is in use" % port))
    else:
        r.append((preflight.PASS, "port", str(port)))
    free = system.disk_free_gb(STRATA_HOME)
    r.append((preflight.PASS if free >= 10 else preflight.WARN, "disk", "%.0f GB free" % free))
    return r


def _strata_env(knobs, force):
    env = dict(os.environ)
    for k in ("PORT", "HOST", "FORCE", "ALLOW_OPEN", "STRATA_TRIAL"):
        env.pop(k, None)  # never inherit a stale knob from the SSH session
    env.update({k: str(v) for k, v in knobs.items() if v not in (None, "") and k != "GPU_BUDGET_GB"})
    env.setdefault("PORT", "8081")
    if env.get("HOST", "127.0.0.1") not in LOOPBACK and not env.get("STRATA_API_KEY"):
        env["ALLOW_OPEN"] = "1"  # LAN without a key: the same exposure as the llama.cpp profiles
    if force:
        env["FORCE"] = "1"
    return env


def _strata_stop():
    p = _sp()
    s = _strata_running()
    if not s:
        return False
    if not p["stop"].exists():
        raise RuntimeError("Strata is running (pid %d) but %s is missing" % (s["pid"], p["stop"]))
    subprocess.run([str(p["stop"])], capture_output=True, text=True, timeout=100, cwd=str(STRATA_HOME))
    if STRATA_STATE.exists():
        STRATA_STATE.unlink()
    return not _strata_running()


# ---------------------------------------------------------------- llama.cpp helpers
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


# ---------------------------------------------------------------- ops
def op_status(_):
    s = backend.running()
    out = {"time": time.time(), "backend": None, "desired": backend.desired()}
    if s:
        code, body = backend.health(s["port"])
        slots = _http_json("http://127.0.0.1:%d/slots" % s["port"]) or []
        out["backend"] = {
            "engine": "llama", "pid": s["pid"], "profile": s["profile"], "port": s["port"], "started": s["started"],
            "uptime_s": time.time() - s["started"], "health": body.get("status") or code,
            "busy_slots": sum(1 for x in slots if isinstance(x, dict) and x.get("is_processing")),
            "slots": len(slots), "flags": _running_flags(s["pid"]),
            "stats": _session_stats(s["profile"]),
        }
        ov = config.overrides_path(s["profile"])
        out["backend"]["settings_changed"] = ov.exists() and ov.stat().st_mtime > s["started"]
    else:
        st = _strata_running()
        if st:
            out["backend"] = _strata_status(st)
    vm = system.vm_stats_gb()
    out["memory"] = {"total": system.mem_total_gb(), "wired": vm["wired"], "active": vm["active"],
                     "compressed": vm["compressed"], "available": vm["free"] + vm["reclaimable"],
                     "swap_mb": system.swap_used_mb(), "wired_limit_mb": system.wired_limit_mb()}
    hb = backend._read(config.RUN / "watchdog.json").get("heartbeat")
    out["watchdog"] = {"installed": agent.loaded(), "heartbeat_age": time.time() - hb if hb else None}
    out["build"] = {"llama_server": config.LLAMA_SERVER.exists(), "strata": _sp()["engine"].exists(),
                    "strata_home": str(STRATA_HOME)}
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
    return {"name": name, "engine": "llama", "description": eff.get("DESCRIPTION", ""), "alias": eff.get("ALIAS"),
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


def _anything_running():
    return bool(backend.running() or _strata_running())


def op_preflight(args):
    profile = config.load_profile(args["profile"])
    results = preflight.run(profile, backend_running=_anything_running())
    st = _strata_running()
    if st:
        results.append((preflight.WARN, "Strata backend", "running (pid %d, profile %s); Restart stops it first" % (st["pid"], st["profile"])))
    return {"ok": preflight.ok(results), "results": [list(r) for r in results]}


def _only_free_memory_failed(results):
    fails = [r for r in results if r[0] == preflight.FAIL]
    return bool(fails) and all(r[1] == "free memory" for r in fails)


def op_start(args):
    profile = config.load_profile(args["profile"])
    st = _strata_running()
    if st:
        raise RuntimeError("Strata is running (pid %d); use Restart to switch engines" % st["pid"])
    if not args.get("force"):
        results = preflight.run(profile, backend_running=bool(backend.running()))
        # Right after a stop the old server's cached model pages still count as used although macOS releases
        # them when the GPU wires its memory (see inference/start.sh). Only that check may be skipped.
        if not preflight.ok(results) and not _only_free_memory_failed(results):
            return {"started": False, "preflight": [list(r) for r in results]}
    pid = backend.start(profile, wait=False, log=lambda *_: None)
    return {"started": True, "pid": pid, "port": profile.port, "engine": "llama"}


def op_stop(_):
    stopped = backend.stop(log=lambda *_: None)   # also clears the watchdog's desired state
    if _strata_running():
        stopped = _strata_stop() or stopped
    return {"stopped": stopped}


def op_strata_preflight(args):
    results = _strata_preflight(args["profile"], args.get("env") or {}, backend_running=_anything_running())
    return {"ok": preflight.ok(results), "results": [list(r) for r in results]}


def op_strata_start(args):
    name, knobs, force = args["profile"], args.get("env") or {}, bool(args.get("force"))
    p = _sp()
    if not p["start"].exists():
        raise RuntimeError("Strata not found: %s is missing. Set the Strata home in Settings → Connection." % p["start"])
    llama = backend.running()
    if llama:
        raise RuntimeError("llama.cpp backend is running (pid %d); use Restart to switch engines" % llama["pid"])
    if _strata_running():
        raise RuntimeError("Strata is already running")
    if not force:
        results = _strata_preflight(name, knobs, backend_running=False)
        if not preflight.ok(results) and not _only_free_memory_failed(results):
            return {"started": False, "preflight": [list(r) for r in results]}
        # llama.cpp may have been stopped a moment ago: strata-start.sh refuses while its memory is still wired.
        for _ in range(90):
            if system.vm_stats_gb()["wired"] < STRATA_WIRED_FREE_GB:
                break
            time.sleep(0.5)
    # Keep the harness watchdog (if installed) from relaunching llama-server beside Strata.
    backend.set_desired(backend.state().get("profile", config.DEFAULT_PROFILE), False)
    config.LOGS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(STRATA_START_LOG, "ab") as out:
        out.write(("\n===== start %s %s =====\n" % (name, time.ctime())).encode())
        out.flush()
        proc = subprocess.Popen([str(p["start"])], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=_strata_env(knobs, force), cwd=str(STRATA_HOME), start_new_session=True)
    # strata-start.sh writes the pid file as soon as the server is launched, then waits for /health on its own
    # (up to 10 minutes). Return once the pid file is there; the app polls status until the model is loaded.
    while time.time() - t0 < 40:
        if proc.poll() is not None:
            raise RuntimeError("strata-start.sh exited with code %s:\n%s"
                               % (proc.returncode, "\n".join(backend.tail(STRATA_START_LOG, 15))))
        s = _strata_running()
        if s and s["started"] >= t0 - 1:
            backend._write(STRATA_STATE, {"pid": s["pid"], "profile": name, "port": s["port"], "started": time.time()})
            return {"started": True, "pid": s["pid"], "port": s["port"], "engine": "strata"}
        time.sleep(0.5)
    raise RuntimeError("Strata did not start within 40 s; see %s" % STRATA_START_LOG)


def op_release_llama(_):
    """Clear the watchdog's wish to run llama.cpp (used when Strata was started outside the app)."""
    backend.set_desired(backend.state().get("profile", config.DEFAULT_PROFILE), False)
    return {"desired": backend.desired()}


def op_logs(args):
    n = int(args.get("lines", 200))
    which = args.get("which") or ("watchdog" if args.get("watchdog") else "auto")
    if which == "auto":
        which = "strata" if (not backend.running() and _strata_running()) else "backend"
    if which == "watchdog":
        path = config.LOGS / "watchdog.log"
    elif which == "strata":
        path = _sp()["server_log"]
        if not path.exists() and STRATA_START_LOG.exists():
            path = STRATA_START_LOG
    elif which == "engine":
        path = _sp()["engine_log"]
    else:
        name = args.get("profile") or backend.state().get("profile", config.DEFAULT_PROFILE)
        path = backend.log_path(name)
    return {"path": str(path), "which": which, "lines": backend.tail(path, n)}


def op_agent(args):
    if args["action"] == "install":
        agent.install()
    else:
        agent.uninstall()
    return {"installed": agent.loaded()}


def op_api_key(args):
    """Effective API key of a profile, for the UI server's proxy only (never sent to the browser)."""
    if args.get("engine") == "strata":
        return {"api_key": _strata_config().get("api_key") or ""}   # what the running server was started with
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
