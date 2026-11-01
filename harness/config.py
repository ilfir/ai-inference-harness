"""Paths and profile loading.

Profiles live in inference/profiles/<name>.env as shell-style KEY=VALUE lines so the same file
drives both inference/serve.sh and this package.
"""

import os
import re
import shlex
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INFERENCE = REPO / "inference"
PROFILES_DIR = INFERENCE / "profiles"

# Runtime home on the machine that runs the backend. In a repo checkout this is the repo itself.
HOME = Path(os.environ.get("AIH_HOME", str(REPO)))
LLAMA_SERVER = HOME / "llama.cpp" / "build" / "bin" / "llama-server"
LOGS = HOME / "logs"
RUN = HOME / "run"
SLOTS = HOME / "slots"

DEFAULT_PROFILE = "default"
DEFAULT_REMOTE_HOME = "~/ai-harness"

_VAR = re.compile(r"\$(\w+)|\$\{(\w+)\}")


def _expand(value, env):
    def sub(m):
        name = m.group(1) or m.group(2)
        return env.get(name, os.environ.get(name, ""))

    return _VAR.sub(sub, value)


def list_profiles():
    return sorted(p.stem for p in PROFILES_DIR.glob("*.env"))


def load_profile(name):
    path = PROFILES_DIR / (name + ".env")
    if not path.exists():
        raise SystemExit("unknown profile %r (have: %s)" % (name, ", ".join(list_profiles())))
    env = {"HOME": str(Path.home())}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        parts = shlex.split(value, comments=True)
        env[key.strip()] = _expand(parts[0] if parts else "", env)
    env.pop("HOME")
    env["NAME"] = name
    return Profile(env)


class Profile(dict):
    """A loaded profile; dict access plus a few typed helpers."""

    @property
    def name(self):
        return self["NAME"]

    @property
    def models_dir(self):
        return Path(self["MODELS_DIR"]).expanduser()

    @property
    def model_path(self):
        return self.models_dir / self["MODEL_FILE"]

    @property
    def mtp_path(self):
        return self.models_dir / self["MTP_FILE"] if self.get("MTP_FILE") else None

    @property
    def port(self):
        return int(self.get("PORT", 8080))

    @property
    def gpu_budget_gb(self):
        return float(self.get("GPU_BUDGET_GB", 0))
