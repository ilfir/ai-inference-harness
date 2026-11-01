"""ai-harness CLI.

Run from the repo root:  python3 -m harness [--host ai@192.168.1.49] <command> [...]
With --host, commands run on the inference host over SSH (the repo is synced first);
chat and bench talk HTTP to the host directly; goose-config always acts on this machine.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config

HTTP_COMMANDS = {"chat", "bench"}
LOCAL_COMMANDS = {"goose-config", "profiles"}


def cmd_profiles(args):
    for name in config.list_profiles():
        p = config.load_profile(name)
        print("%-14s %s" % (name, p.get("DESCRIPTION", "")))


def cmd_build(args):
    extra = ["--clean"] if args.clean else []
    if args.commit:
        extra += ["--commit", args.commit]
    return subprocess.call([str(config.INFERENCE / "build.sh")] + extra,
                           env=dict(os.environ, AIH_HOME=str(config.HOME)))


def cmd_download(args):
    from . import backend

    log = config.LOGS / ("download-%s.log" % args.profile)
    if args.status:
        p = config.load_profile(args.profile)
        size = sum(f.stat().st_size for f in p.models_dir.rglob("*") if f.is_file()) / 1e9 \
            if p.models_dir.exists() else 0
        print("%s: %.1f GB on disk in %s" % (args.profile, size, p.models_dir))
        print("\n".join(backend.tail(log, 3)))
        return 0
    config.LOGS.mkdir(parents=True, exist_ok=True)
    if args.foreground:
        return subprocess.call([str(config.INFERENCE / "download.sh"), args.profile])
    with open(log, "ab") as out:
        proc = subprocess.Popen([str(config.INFERENCE / "download.sh"), args.profile], stdout=out,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    print("download started (pid %d), log %s; check with: download --status %s" % (proc.pid, log, args.profile))
    return 0


def cmd_preflight(args):
    from . import backend, preflight

    results = preflight.run(config.load_profile(args.profile), backend_running=bool(backend.running()))
    print(preflight.render(results))
    return 0 if preflight.ok(results) else 1


def cmd_serve(args):
    from . import backend, preflight

    profile = config.load_profile(args.profile)
    if not args.force:
        results = preflight.run(profile, backend_running=bool(backend.running()))
        print(preflight.render(results))
        if not preflight.ok(results):
            print("\npreflight failed; fix the ❌ items or pass --force")
            return 1
    try:
        backend.start(profile, wait=not args.no_wait, timeout=args.timeout)
    except RuntimeError as e:
        print("error: %s" % e)
        return 1
    return 0


def cmd_stop(args):
    from . import backend

    backend.stop()
    return 0


def cmd_restart(args):
    from . import backend

    name = args.profile or backend.state().get("profile", config.DEFAULT_PROFILE)
    backend.stop()
    args.profile = name
    return cmd_serve(args)


def cmd_status(args):
    from . import agent, backend, system

    s = backend.running()
    if s:
        code, body = backend.health(s["port"])
        up = (time.time() - s["started"]) / 60
        print("backend   running  pid %d  profile %s  port %d  up %.0f min  health %s"
              % (s["pid"], s["profile"], s["port"], up, body.get("status", code)))
        speed, accept = backend.recent_stats(s["profile"])
        if speed:
            print("last gen  " + speed)
        if accept:
            print("mtp       " + accept)
    else:
        want = backend.desired()
        print("backend   stopped" + ("  (watchdog should restart profile %s)" % want["profile"]
                                     if want.get("running") else ""))
    vm = system.vm_stats_gb()
    print("memory    wired %.1f GB  active %.1f GB  available %.1f GB  swap used %.0f MB  of %.0f GB"
          % (vm["wired"], vm["active"], vm["free"] + vm["reclaimable"], system.swap_used_mb(), system.mem_total_gb()))
    print("gpu limit iogpu.wired_limit_mb=%s" % (system.wired_limit_mb() or "default"))
    hb = backend._read(config.RUN / "watchdog.json").get("heartbeat")
    wd = "loaded" if agent.loaded() else "not installed"
    if hb and (agent.loaded() or time.time() - hb < 60):
        wd += ", heartbeat %.0f s ago" % (time.time() - hb)
    print("watchdog  " + wd)
    return 0


def cmd_logs(args):
    from . import backend

    name = args.profile or backend.state().get("profile", config.DEFAULT_PROFILE)
    path = config.LOGS / ("watchdog.log" if args.watchdog else "backend-%s.log" % name)
    if args.follow:
        return subprocess.call(["tail", "-n", str(args.lines), "-f", str(path)])
    print("\n".join(backend.tail(path, args.lines)))
    return 0


def cmd_watch(args):
    from . import watchdog

    watchdog.run()


def cmd_install_agent(args):
    from . import agent

    agent.install()


def cmd_uninstall_agent(args):
    from . import agent

    agent.uninstall()


def _base_url(args, profile):
    host = "127.0.0.1"
    if args.host:
        from .remote import http_host

        host = http_host(args.host)
    return "http://%s:%d" % (host, profile.port)


def cmd_chat(args):
    from .client import Client

    profile = config.load_profile(args.profile)
    client = Client(_base_url(args, profile), os.environ.get("AIH_API_KEY"))
    thinking = not args.no_think
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    print("chat with %s at %s (thinking %s). /think, /nothink, /reset, /quit"
          % (profile["ALIAS"], client.base_url, "on" if thinking else "off"))
    dim, reset = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text in ("/think", "/nothink"):
            thinking = text == "/think"
            print("thinking %s" % ("on" if thinking else "off"))
            continue
        if text == "/reset":
            messages = messages[:1] if args.system else []
            continue
        messages.append({"role": "user", "content": text})
        answer, final, in_reasoning = [], {}, False
        try:
            for kind, piece in client.stream(messages, thinking=thinking, model=profile["ALIAS"]):
                if kind == "reasoning":
                    if not in_reasoning:
                        sys.stdout.write(dim)
                        in_reasoning = True
                    sys.stdout.write(piece)
                elif kind == "content":
                    if in_reasoning:
                        sys.stdout.write(reset + "\n\n")
                        in_reasoning = False
                    sys.stdout.write(piece)
                    answer.append(piece)
                else:
                    final = piece
                sys.stdout.flush()
        except KeyboardInterrupt:
            print(reset + "\n[interrupted]")
        except OSError as e:
            print(reset + "\nrequest failed: %s" % e)
            messages.pop()
            continue
        if in_reasoning:
            sys.stdout.write(reset)
        t = final.get("timings", {})
        if t:
            print("\n%s[%d tok, %.1f tok/s]%s" % (dim, t.get("predicted_n", 0), t.get("predicted_per_second", 0), reset))
        messages.append({"role": "assistant", "content": "".join(answer)})


def cmd_bench(args):
    from . import bench

    profile = config.load_profile(args.profile)
    url = _base_url(args, profile)
    print("benchmarking %s (fill ~%d tokens, %d max tokens)" % (url, args.fill, args.max_tokens))
    model, rows = bench.run(url, model=profile["ALIAS"], max_tokens=args.max_tokens, fill=args.fill,
                            temperature=args.temperature, runs=args.runs, api_key=os.environ.get("AIH_API_KEY"))
    report = bench.markdown(model, rows, args.fill)
    print("\n" + report)
    if args.out:
        Path(args.out).write_text(report + "\n")
        print("\nwritten to %s" % args.out)
    return 0


def cmd_goose_config(args):
    src = config.REPO / "client" / "goose" / "qwen_flash_next.json"
    data = json.loads(src.read_text())
    if args.host:
        from .remote import http_host

        data["base_url"] = "http://%s:%d/v1" % (http_host(args.host), config.load_profile(args.profile).port)
    text = json.dumps(data, indent=2) + "\n"
    if not args.install:
        print(text)
        return 0
    target = Path.home() / ".config" / "goose" / "custom_providers" / src.name
    if target.exists():
        print("%s already exists; not overwriting" % target)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    print("installed %s; restart Goose and pick %r" % (target, data["display_name"]))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="harness", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="run against this SSH host, e.g. ai@192.168.1.49")
    ap.add_argument("--remote-home", default=config.DEFAULT_REMOTE_HOME, help="harness home on the host")
    ap.add_argument("--no-sync", action="store_true", help="skip rsync of the repo to the host")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name, fn, help_text, profile=True):
        p = sub.add_parser(name, help=help_text)
        if profile:
            p.add_argument("profile", nargs="?", default=config.DEFAULT_PROFILE)
        p.set_defaults(fn=fn)
        return p

    add("profiles", cmd_profiles, "list serving profiles", profile=False)
    add("sync", None, "copy inference/, harness/, client/ to --host", profile=False)
    p = add("build", cmd_build, "build llama.cpp (Metal) at the pinned commit", profile=False)
    p.add_argument("--commit")
    p.add_argument("--clean", action="store_true")
    p = add("download", cmd_download, "download model files for a profile (background)")
    p.add_argument("--status", action="store_true")
    p.add_argument("--foreground", action="store_true")
    add("preflight", cmd_preflight, "check everything needed to serve a profile")
    for name, fn in (("serve", cmd_serve), ("restart", cmd_restart)):
        p = add(name, fn, "%s the backend" % name)
        if name == "restart":
            p.set_defaults(profile=None)
        p.add_argument("--force", action="store_true", help="start even if preflight fails")
        p.add_argument("--no-wait", action="store_true")
        p.add_argument("--timeout", type=int, default=900)
    add("stop", cmd_stop, "stop the backend", profile=False)
    add("status", cmd_status, "backend, memory and watchdog status", profile=False)
    p = add("logs", cmd_logs, "show backend (or watchdog) log")
    p.set_defaults(profile=None)
    p.add_argument("-n", "--lines", type=int, default=40)
    p.add_argument("-f", "--follow", action="store_true")
    p.add_argument("--watchdog", action="store_true")
    add("watch", cmd_watch, "run the watchdog loop (launchd runs this)", profile=False)
    add("install-agent", cmd_install_agent, "install the watchdog LaunchAgent", profile=False)
    add("uninstall-agent", cmd_uninstall_agent, "remove the watchdog LaunchAgent", profile=False)
    p = add("chat", cmd_chat, "interactive chat over the OpenAI API")
    p.add_argument("--no-think", action="store_true")
    p.add_argument("--system")
    p = add("bench", cmd_bench, "measure prefill/decode speed and MTP acceptance")
    p.add_argument("--fill", type=int, default=0, help="approximate context tokens to prefill first")
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--temperature", type=float)
    p.add_argument("--runs", type=int)
    p.add_argument("--out", help="write the markdown report here")
    p = add("goose-config", cmd_goose_config, "print or install the Goose provider on this machine")
    p.add_argument("--install", action="store_true")
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        if not args.host:
            raise SystemExit("sync needs --host")
        from . import remote

        remote.sync(args.host, args.remote_home)
        print("synced to %s:%s" % (args.host, args.remote_home))
        return 0
    if args.host and args.command not in HTTP_COMMANDS | LOCAL_COMMANDS:
        from . import remote

        if not args.no_sync:
            remote.sync(args.host, args.remote_home)
        inner = list(argv)
        # drop the global options; the host runs the command locally
        for flag in ("--host", "--remote-home"):
            while flag in inner:
                i = inner.index(flag)
                del inner[i:i + 2]
        inner = [a for a in inner if a != "--no-sync"]
        return remote.run(args.host, args.remote_home, inner, tty=args.command in ("logs",))
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
