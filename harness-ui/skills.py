"""Skills for the chat model, in the way Goose uses them (Agent Skills: a folder with a SKILL.md and optional scripts).

Discovery: every folder that contains a SKILL.md under ~/.agents/skills, ~/.claude/skills and ~/.config/goose/skills
(more roots through HARNESS_SKILLS_DIRS, colon separated). A skill is a name and a description from the SKILL.md front
matter. The system prompt lists them; the model calls load_skill to read the full instructions, and run_skill_script
to run a script that ships inside the skill (no shell: the script is resolved inside the skill's folder, arguments are
passed as a list). To add a skill, drop its folder into one of those roots; nothing else to register.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_OUT = 12000          # characters of script output returned per call
MAX_DOC = 20000          # characters of SKILL.md returned
RUN_TIMEOUT = 120        # seconds per script run
HOME = Path.home()
DEFAULT_ROOTS = [HOME / ".agents" / "skills", HOME / ".claude" / "skills", HOME / ".config" / "goose" / "skills"]
TOOL_PATHS = [str(HOME / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]

TOOLS = [
    {"type": "function", "function": {
        "name": "load_skill",
        "description": "Load a skill's full instructions (SKILL.md) and the list of files that ship with it. Call "
                       "this when a request matches a skill in the available-skills list, before doing the task.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Skill name, as listed"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "run_skill_script",
        "description": "Run a script that ships inside a skill (for example scripts/get_transcript.py) and return "
                       "its output and exit code. Python and shell scripts are supported. Give the script path "
                       "relative to the skill folder and the arguments as a list; do not include the interpreter "
                       "or 'uv run'.",
        "parameters": {"type": "object", "properties": {
            "skill": {"type": "string", "description": "Skill name"},
            "script": {"type": "string", "description": "Script path relative to the skill, e.g. scripts/get_transcript.py"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Command-line arguments"}},
            "required": ["skill", "script"]}}},
]
NAMES = {t["function"]["name"] for t in TOOLS}


def roots():
    extra = [Path(os.path.expanduser(p)) for p in os.environ.get("HARNESS_SKILLS_DIRS", "").split(":") if p]
    return DEFAULT_ROOTS + extra


def _front_matter(text):
    """(fields, body) from a SKILL.md with a leading --- block of simple `key: value` lines."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text
    fields = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip() and not line.startswith((" ", "\t")):
            fields[k.strip()] = v.strip().strip("\"'")
    return fields, text[m.end():]


def discover():
    """{name: {name, description, dir}}; earlier roots win on a name clash."""
    found = {}
    for root in roots():
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for d in entries:
            md = d / "SKILL.md"
            try:
                if not md.is_file():
                    continue
                fields, _ = _front_matter(md.read_text("utf-8", "replace"))
            except OSError:
                continue
            name = fields.get("name") or d.name
            if name not in found:
                found[name] = {"name": name, "description": fields.get("description", ""), "dir": str(d.resolve())}
    return found


def listing():
    """What the UI shows: name and description of each available skill."""
    return [{"name": s["name"], "description": s["description"], "dir": s["dir"]} for s in discover().values()]


def system_hint():
    skills = discover()
    if not skills:
        return ""
    lines = "\n".join("- %s: %s" % (s["name"], s["description"]) for s in skills.values())
    return ("You can use skills: packaged instructions and scripts for specific tasks. Available skills:\n%s\n"
            "When the user's request matches a skill, call load_skill with its name first and follow the "
            "instructions it returns. Scripts that ship with a skill are run with run_skill_script (script path "
            "relative to the skill, arguments as a list). Report script failures as they are; never make up what "
            "a script should have returned." % lines)


def _skill(name):
    s = discover().get(name)
    if not s:
        raise ValueError("no such skill: %s (available: %s)" % (name, ", ".join(discover()) or "none"))
    return s


def load_skill(name):
    s = _skill(name)
    d = Path(s["dir"])
    _, body = _front_matter((d / "SKILL.md").read_text("utf-8", "replace"))
    files = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md"
                   and not any(part.startswith(".") for part in p.relative_to(d).parts))[:100]
    return {"name": s["name"], "instructions": body[:MAX_DOC], "truncated": len(body) > MAX_DOC, "files": files,
            "note": "Run scripts with run_skill_script, giving the path relative to the skill and a list of "
                    "arguments, e.g. script 'scripts/x.py'. The interpreter (uv or python3) is chosen for you."}


def _command(script):
    """Interpreter command for a script file. Python scripts go through uv when present (it installs the
    dependencies declared in the script's header), otherwise through python3."""
    path = os.pathsep.join(TOOL_PATHS)
    if script.suffix == ".py":
        uv = shutil.which("uv", path=path)
        if uv:
            return [uv, "run", "--quiet", str(script)]
        return [shutil.which("python3", path=path) or sys.executable, str(script)]
    if script.suffix in (".sh", ".bash"):
        return ["/bin/bash", str(script)]
    raise ValueError("only .py and .sh scripts can be run (got %s)" % (script.suffix or "no extension"))


def run_skill_script(skill, script, args=None):
    s = _skill(skill)
    root = Path(s["dir"])
    rel = (script or "").strip()
    if not rel or rel.startswith(("/", "~")):
        raise ValueError("give the script path relative to the skill folder")
    p = (root / rel).resolve()
    if root not in p.parents or not p.is_file():
        raise ValueError("no such script in the skill: %s" % rel)
    if not isinstance(args, list):
        args = [] if args in (None, "") else [args]
    args = [str(a) for a in args][:40]
    env = dict(os.environ, PATH=os.pathsep.join(TOOL_PATHS + [os.environ.get("PATH", "")]), PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(_command(p) + args, cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ValueError("script timed out after %d seconds" % RUN_TIMEOUT)
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    res = {"exit_code": r.returncode, "stdout": out[:MAX_OUT], "stdout_truncated": len(out) > MAX_OUT,
           "stderr": err[-3000:]}
    if len(out) > MAX_OUT:
        res["note"] = "Output was cut. Run again with the script's own options to write to a file or limit it."
    return res


def call(name, arguments):
    """Run one skill tool call; always returns a JSON string for the model."""
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else (arguments or {})
        if not isinstance(args, dict):
            args = {}
        if name == "load_skill":
            out = load_skill(args.get("name", ""))
        elif name == "run_skill_script":
            out = run_skill_script(args.get("skill", ""), args.get("script", ""), args.get("args"))
        else:
            out = {"error": "unknown tool " + name}
    except Exception as e:
        out = {"error": "%s: %s" % (type(e).__name__, e)}
    return json.dumps(out, ensure_ascii=False)


def summary(name, parsed):
    if "error" in parsed:
        return parsed["error"]
    if name == "load_skill":
        return "%d files" % len(parsed.get("files", []))
    return "exit %s, %d chars" % (parsed.get("exit_code"), len(parsed.get("stdout", "")))
