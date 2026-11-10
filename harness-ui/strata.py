"""Strata backend profiles for harness-ui.

The second engine the app can drive is Strata, prepared on the inference host by the ai-inference-strata trial
(`<strata home>/scripts/strata-start.sh`, `strata-stop.sh`). Its profiles live in harness-ui/strata-profiles/<name>.env
and each key is an environment knob of strata-start.sh (read by the trial's make-config.py). Unlike the llama.cpp
profiles, nothing on the host reads these files: the app sends the effective environment to ops.py with every start,
so the host needs only the Strata directory itself. Per-machine overrides are therefore kept on this Mac, next to the
connection settings (strata-overrides.json, mode 600 because it may hold an API key).
"""

import json
import os
import re
import threading
from pathlib import Path

from harness import config

ENGINE = "strata"
LOOPBACK = ("127.0.0.1", "localhost", "::1")

# Editable settings: key -> (type, constraint, label, help). Same shape as ops.SETTINGS for the llama.cpp profiles.
SETTINGS = {
    "CTX": ("int", [8192, 16384, 32768, 65536, 131072, 262144], "Context size (tokens)",
            "Engine --max-context. Every slot gets its own KV cache (about 2.3 GB per slot at 128k, fp16)."),
    "PARALLEL": ("int", (1, 8), "Parallel requests",
                 "Batch slots (engine --batch N). The trial measured no gain from 2; slot save/restore needs 1."),
    "MTP": ("bool", None, "MTP speculative decoding",
            "Draft with the packed MTP layer (Strata-data/mtp, made by prepare.sh --download-mtp). "
            "Measured +0-10% in Strata, within noise."),
    "SPEC": ("int", (2, 4), "Speculative window",
             "--spec N: a window of N rows is N-1 draft tokens. 2 matches llama.cpp's draft length 1."),
    "SUFFIX_DRAFT": ("enum", ["0", "default"], "Prompt-lookup drafter",
                     "0 = off (measured best when streaming); default = the engine's own setting (on, 3 tokens)."),
    "STREAM": ("enum", ["0", "1", "auto"], "Expert weights",
               "0 = resident on the GPU (57-61 GB wired, 1.35x llama.cpp decode; outside Strata's tested envelope "
               "on 64 GB). 1 = streamed from the SSD (~35 GB, ~0.5x). auto = the engine's planner, which streams on 64 GB."),
    "KV_TYPE": ("enum", ["", "fp16", "int8", "q4_0", "k8v4"], "KV cache type",
                "Engine --kv. Empty = engine default (fp16). int8 is the nearest to llama.cpp's q8_0 but still gated by the author."),
    "CONV_CACHE_MIB": ("int", (0, 16384), "Conversation cache (MiB)",
                       "Host RAM for parked conversations, like llama-server --cache-ram. 0 disables."),
    "BATCH_MTP": ("bool", None, "MTP inside batch windows",
                  "--batch-mtp: keep drafting while 2+ requests run together. Metal-untested; only with MTP and 2+ slots."),
    "PORT": ("int", (1024, 65535), "Port", "HTTP port of the OpenAI-compatible API (Strata also serves Anthropic /v1/messages there)."),
    "HOST": ("enum", ["0.0.0.0", "127.0.0.1"], "Listen address",
             "0.0.0.0 serves the LAN (without an API key the app passes ALLOW_OPEN=1, the same exposure as llama-server); "
             "127.0.0.1 only the host itself."),
    "STRATA_API_KEY": ("secret", 128, "API key", "If set, clients must send 'Authorization: Bearer <key>' (or x-api-key)."),
    "GPU_BUDGET_GB": ("float", (1, 128), "Memory budget (GB)",
                      "Wired memory this configuration reaches; preflight checks free memory and the GPU wired limit against it."),
    "EXTRA_ARGS": ("text", 500, "Extra engine arguments", "Appended verbatim to the strata engine command line."),
}
DEFAULTS = {"CTX": "131072", "PARALLEL": "2", "MTP": "1", "SPEC": "2", "SUFFIX_DRAFT": "0", "STREAM": "1",
            "KV_TYPE": "", "CONV_CACHE_MIB": "2048", "BATCH_MTP": "0", "PORT": "8081", "HOST": "127.0.0.1",
            "STRATA_API_KEY": "", "GPU_BUDGET_GB": "57", "EXTRA_ARGS": ""}
# Knobs forwarded to strata-start.sh (everything editable except the preflight-only budget).
ENV_KEYS = [k for k in SETTINGS if k != "GPU_BUDGET_GB"]
MASK = "••••••"


def validate(key, value):
    """Normalise one setting to its environment string; raise ValueError when it is out of range."""
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
        return "%g" % v
    if typ == "enum":
        if value not in cons:
            raise ValueError("%s must be one of %s" % (label, [c or "(default)" for c in cons]))
        return value
    if typ in ("text", "secret"):
        value = str(value)
        if "\n" in value or "\r" in value or len(value) > cons:
            raise ValueError("%s must be one line, at most %d characters" % (label, cons))
        if typ == "secret" and re.search(r"\s", value):
            raise ValueError("%s must not contain spaces" % label)
        return value
    if typ == "bool":
        return "1" if value in (True, "1", "true", "on") else "0"
    raise ValueError("unknown setting " + key)


class Store:
    """The Strata profiles (repo files) plus the local overrides file."""

    def __init__(self, profiles_dir, overrides_path):
        self.dir = Path(profiles_dir)
        self.path = Path(overrides_path)
        self.lock = threading.Lock()

    def names(self):
        return sorted(p.stem for p in self.dir.glob("*.env"))

    def has(self, name):
        return (self.dir / (name + ".env")).is_file()

    def _base(self, name):
        path = self.dir / (name + ".env")
        if not path.is_file():
            raise KeyError(name)
        env = {"HOME": str(Path.home())}
        config._read_env(path, env)
        env.pop("HOME")
        return env

    def _overrides(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.chmod(str(tmp), 0o600)
        tmp.replace(self.path)

    def effective(self, name):
        """Base profile with this Mac's overrides applied: {KEY: str}."""
        env = self._base(name)
        env.update(self._overrides().get(name, {}))
        return env

    def env(self, name):
        """What strata-start.sh is run with: the knobs that have a value."""
        eff = self.effective(name)
        out = {k: str(eff.get(k, DEFAULTS.get(k, ""))) for k in ENV_KEYS}
        out = {k: v for k, v in out.items() if v != ""}
        out["GPU_BUDGET_GB"] = str(eff.get("GPU_BUDGET_GB", DEFAULTS["GPU_BUDGET_GB"]))  # preflight only
        return out

    def view(self, name):
        base, eff = self._base(name), self.effective(name)
        ov = self._overrides().get(name, {})

        def val(p, key):
            v = p.get(key, DEFAULTS.get(key, ""))
            return v == "1" if SETTINGS[key][0] == "bool" else v

        settings = {}
        for key, (typ, cons, label, help_text) in SETTINGS.items():
            settings[key] = {
                "type": typ, "label": label, "help": help_text,
                "choices": cons if isinstance(cons, list) else None,
                "range": list(cons) if isinstance(cons, tuple) else None,
                "value": val(eff, key), "default": val(base, key), "overridden": key in ov,
            }
            if typ == "secret":
                settings[key]["value"] = MASK if eff.get(key) else ""
                settings[key]["is_set"] = bool(eff.get(key))
        return {"name": name, "engine": ENGINE, "description": eff.get("DESCRIPTION", ""), "alias": eff.get("ALIAS"),
                "model": "Qwen3.8-Flash-Next IQ3_XXS (Strata pack)", "mtp_available": True, "settings": settings}

    def views(self):
        return [self.view(n) for n in self.names()]

    def save(self, name, values):
        base = self._base(name)
        with self.lock:
            data = self._overrides()
            current = dict(data.get(name, {}))
            for key, value in values.items():
                if key not in SETTINGS:
                    raise ValueError("unknown setting " + key)
                if SETTINGS[key][0] == "secret" and value == MASK:
                    continue  # unchanged placeholder
                v = validate(key, value)
                if v == base.get(key, DEFAULTS.get(key, "")):
                    current.pop(key, None)
                else:
                    current[key] = v
            if current:
                data[name] = current
            else:
                data.pop(name, None)
            self._write(data)
        return self.view(name)

    def reset(self, name):
        with self.lock:
            data = self._overrides()
            data.pop(name, None)
            self._write(data)
        return self.view(name)
