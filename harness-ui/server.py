"""harness-ui: local web app to control the inference backend.

    python3 harness-ui/server.py [--host ai@192.168.1.49] [--port 8765] [--no-browser]

Serves the UI on http://127.0.0.1:8765 (this machine only). Host operations run harness-ui/ops.py on the
inference machine over SSH (or locally without --host). Chat and benchmarks are proxied to the backend's
OpenAI-compatible API. Standard library only, like the CLI.
"""

import argparse
import datetime
import json
import mimetypes
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

UI = Path(__file__).resolve().parent
REPO = UI.parent
sys.path.insert(0, str(REPO))

from harness import bench, remote  # noqa: E402

import webtools  # noqa: E402
from chats import MAX_CHATS, ChatStore  # noqa: E402

STATIC = UI / "static"
SETTINGS_FILE = Path(os.environ.get("AIH_UI_SETTINGS") or UI / "settings.json")
CHATS = ChatStore(os.environ.get("AIH_UI_CHATS") or SETTINGS_FILE.parent / "chats.json")
OPS = (UI / "ops.py").read_text()
MARK = "@@AIH_JSON@@"


class State:
    def __init__(self, host, remote_home):
        saved = {}
        if SETTINGS_FILE.exists():
            try:
                saved = json.loads(SETTINGS_FILE.read_text())
            except ValueError:
                pass
        self.host = host if host is not None else saved.get("host", "")
        self.remote_home = remote_home or saved.get("remote_home", "~/ai-harness")
        self.lock = threading.Lock()
        self.synced = False
        self.bench_lock = threading.Lock()

    def save(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps({"host": self.host, "remote_home": self.remote_home}, indent=2))

    def http_host(self):
        return remote.http_host(self.host) if self.host else "127.0.0.1"

    def ssh_base(self):
        # Reuse one SSH connection for the frequent status polls.
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlMaster=auto",
                "-o", "ControlPath=/tmp/aih-ui-%r@%h:%p", "-o", "ControlPersist=120", self.host]


STATE = None


class OpError(Exception):
    pass


def run_op(op, args=None, timeout=60):
    payload = json.dumps(args or {})
    if STATE.host:
        home = STATE.remote_home
        home = home.replace("~", "$HOME", 1) if home.startswith("~") else home
        inner = 'cd "%s" && AIH_HOME="%s" /usr/bin/python3 - %s %s' % (
            home, home, op, "'" + payload.replace("'", "'\\''") + "'")
        cmd = STATE.ssh_base() + [inner]
        cwd = None
    else:
        cmd = [sys.executable, "-", op, payload]
        cwd = str(REPO)
    try:
        p = subprocess.run(cmd, input=OPS, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise OpError("%s timed out" % op)
    if MARK not in p.stdout:
        raise OpError((p.stderr or p.stdout or "no output").strip()[-600:])
    result = json.loads(p.stdout.split(MARK, 1)[1])
    if not result.get("ok"):
        raise OpError(result.get("error", "failed"))
    return result["data"]


def ensure_synced(force=False):
    """Copy inference/, harness/, client/ to the host once per UI session (and before starts)."""
    if STATE.host and (force or not STATE.synced):
        remote.sync(STATE.host, STATE.remote_home)
        STATE.synced = True


def backend_url():
    st = run_op("status")
    b = st.get("backend")
    if not b:
        raise OpError("backend is not running")
    return "http://%s:%d" % (STATE.http_host(), b["port"]), b["profile"]


_key_cache = {}


def api_key_for(profile):
    if profile not in _key_cache:
        _key_cache[profile] = run_op("api_key", {"profile": profile}).get("api_key", "")
    return _key_cache[profile]


class Handler(BaseHTTPRequestHandler):
    server_version = "harness-ui"

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- helpers --
    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _guard(self):
        # Only same-origin browser requests may drive the host (blocks cross-site POSTs).
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._json({"error": "cross-origin request refused"}, 403)
            return False
        return True

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC not in f.parents or not f.is_file():
            self.send_error(404)
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if not path.startswith("/api/"):
            if method == "GET":
                return self._static(path)
            return self.send_error(404)
        if method != "GET" and not self._guard():
            return
        try:
            if path == "/api/config" and method == "GET":
                return self._json({"host": STATE.host, "remote_home": STATE.remote_home,
                                   "http_host": STATE.http_host()})
            if path == "/api/config" and method == "POST":
                b = self._body()
                STATE.host = b.get("host", STATE.host).strip()
                STATE.remote_home = b.get("remote_home", STATE.remote_home).strip() or "~/ai-harness"
                STATE.synced = False
                _key_cache.clear()
                STATE.save()
                return self._json({"host": STATE.host, "remote_home": STATE.remote_home,
                                   "http_host": STATE.http_host()})
            if path == "/api/status":
                return self._json(run_op("status", timeout=20))
            if path == "/api/profiles" and method == "GET":
                ensure_synced()
                return self._json(run_op("profiles"))
            if path.startswith("/api/profiles/"):
                name = path.rsplit("/", 1)[1]
                _key_cache.pop(name, None)
                if method == "POST":
                    return self._json(run_op("save_settings", {"profile": name, "values": self._body()}))
                if method == "DELETE":
                    return self._json(run_op("reset_settings", {"profile": name}))
            if path == "/api/preflight":
                ensure_synced()
                return self._json(run_op("preflight", self._body(), timeout=120))
            if path == "/api/start":
                ensure_synced(force=True)
                return self._json(run_op("start", self._body(), timeout=120))
            if path == "/api/stop":
                return self._json(run_op("stop", timeout=60))
            if path == "/api/restart":
                b = self._body()
                run_op("stop", timeout=60)
                ensure_synced(force=True)
                return self._json(run_op("start", b, timeout=120))
            if path == "/api/logs":
                args = {"lines": int(query.get("lines", ["200"])[0]),
                        "watchdog": query.get("watchdog", ["0"])[0] == "1"}
                return self._json(run_op("logs", args))
            if path == "/api/agent":
                ensure_synced()
                return self._json(run_op("agent", self._body(), timeout=60))
            if path == "/api/bench":
                return self._bench(self._body())
            if path == "/api/chat":
                return self._chat(self._body())
            if path == "/api/chats" and method == "GET":
                return self._json({"chats": CHATS.list(), "max": MAX_CHATS})
            if path.startswith("/api/chats/"):
                chat_id = path.rsplit("/", 1)[1]
                try:
                    if method == "GET":
                        return self._json(CHATS.get(chat_id))
                    if method == "POST":
                        summary, pruned = CHATS.save(chat_id, self._body())
                        return self._json({"chat": summary, "pruned": pruned})
                    if method == "DELETE":
                        CHATS.delete(chat_id)
                        return self._json({"ok": True})
                except KeyError:
                    return self._json({"error": "chat not found"}, 404)
            return self._json({"error": "not found"}, 404)
        except OpError as e:
            return self._json({"error": str(e)}, 502)
        except (ValueError, KeyError) as e:
            return self._json({"error": "bad request: %s" % e}, 400)
        except subprocess.CalledProcessError as e:
            return self._json({"error": "command failed: %s" % e}, 502)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    # -- backend proxies --
    def _bench(self, b):
        if not STATE.bench_lock.acquire(blocking=False):
            return self._json({"error": "a benchmark is already running"}, 409)
        try:
            url, profile = backend_url()
            model, rows = bench.run(url, model=None, max_tokens=int(b.get("max_tokens", 256)),
                                    fill=int(b.get("fill", 0)), temperature=b.get("temperature"),
                                    runs=int(b.get("runs", 3)), api_key=api_key_for(profile) or None,
                                    log=lambda *_: None)
            return self._json({"model": model, "profile": profile, "fill": int(b.get("fill", 0)), "rows": rows})
        except urllib.error.URLError as e:
            return self._json({"error": "backend request failed: %s" % e}, 502)
        finally:
            STATE.bench_lock.release()

    def _upstream(self, url, profile, body):
        req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        key = api_key_for(profile)
        if key:
            req.add_header("Authorization", "Bearer " + key)
        return urllib.request.urlopen(req, timeout=1800)

    def _sse(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def _chat(self, body):
        url, profile = backend_url()
        web = bool(body.pop("web", False))
        body["stream"] = True
        if web:
            body["tools"] = webtools.TOOLS
            hint = webtools.SYSTEM_HINT.format(date=datetime.date.today().isoformat())
            msgs = body["messages"]
            if msgs and msgs[0].get("role") == "system":
                msgs[0] = dict(msgs[0], content=msgs[0]["content"] + "\n\n" + hint)
            else:
                msgs.insert(0, {"role": "system", "content": hint})
        try:
            upstream = self._upstream(url, profile, body)
        except urllib.error.HTTPError as e:
            return self._json({"error": "backend %d: %s" % (e.code, e.read()[:300].decode("utf-8", "replace"))}, 502)
        except urllib.error.URLError as e:
            return self._json({"error": "backend unreachable: %s" % e}, 502)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            if not web:
                with upstream:
                    for line in upstream:
                        self.wfile.write(line)
                        if line.strip() == b"" or line.startswith(b"data:"):
                            self.wfile.flush()
                return
            self._tool_loop(url, profile, body, upstream)
        except urllib.error.URLError as e:
            self._sse({"error": "backend request failed: %s" % e})
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser stopped the generation; closing upstream cancels it on the server

    MAX_TOOL_ROUNDS = 8

    def _tool_loop(self, url, profile, body, upstream):
        """Stream rounds to the browser, running web tool calls between them, until the model answers."""
        for round_no in range(self.MAX_TOOL_ROUNDS + 1):
            calls, content, reasoning, finish = {}, "", "", None
            with upstream:
                for line in upstream:
                    if not line.startswith(b"data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == b"[DONE]":
                        continue
                    chunk = json.loads(payload)
                    for ch in chunk.get("choices") or []:
                        d = ch.get("delta") or {}
                        content += d.get("content") or ""
                        reasoning += d.get("reasoning_content") or ""
                        for tc in d.get("tool_calls") or []:
                            c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            c["id"] = tc.get("id") or c["id"]
                            fn = tc.get("function") or {}
                            c["name"] += fn.get("name") or ""
                            c["arguments"] += fn.get("arguments") or ""
                        finish = ch.get("finish_reason") or finish
                    self._sse(chunk)
            if not calls:
                break
            if round_no == self.MAX_TOOL_ROUNDS:
                # Ignored the "no tools" request (or hit the cap some other way): nothing more to run.
                break
            ordered = [calls[i] for i in sorted(calls)]
            msg = {"role": "assistant", "content": content, "tool_calls": [
                {"id": c["id"] or "call_%d_%d" % (round_no, i), "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}} for i, c in enumerate(ordered)]}
            if reasoning:
                msg["reasoning_content"] = reasoning
            body["messages"].append(msg)
            for call, tc in zip(ordered, msg["tool_calls"]):
                try:
                    args = json.loads(call["arguments"] or "{}")
                except ValueError:
                    args = {}
                self._sse({"tool_event": {"name": call["name"], "args": args, "status": "running"}})
                result = webtools.call(call["name"], call["arguments"])
                parsed = json.loads(result)
                if "error" in parsed:
                    summary = parsed["error"]
                elif call["name"] == "web_search":
                    summary = "%d results" % len(parsed.get("results", []))
                else:
                    summary = parsed.get("title") or parsed.get("url", "")
                self._sse({"tool_event": {"name": call["name"], "args": args, "status": "done",
                                          "ok": "error" not in parsed, "summary": summary}})
                body["messages"].append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            if round_no + 1 == self.MAX_TOOL_ROUNDS:
                # Last round: remove the tools so the model must answer with what it has gathered.
                body.pop("tools", None)
                body.pop("tool_choice", None)
                body["messages"].append({"role": "user", "content": "You have reached the limit for web lookups. "
                    "Using what you found so far, write the final answer now. Do not call any tools; "
                    "say what you could not confirm."})
            upstream = self._upstream(url, profile, body)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    global STATE
    ap = argparse.ArgumentParser(description="harness-ui web app")
    ap.add_argument("--host", help="SSH target of the inference machine, e.g. ai@192.168.1.49 (default: saved)")
    ap.add_argument("--remote-home", help="harness home on the host (default ~/ai-harness)")
    ap.add_argument("--port", type=int, default=8765, help="0 picks a free port")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    STATE = State(args.host, args.remote_home)
    if args.host is not None:
        STATE.save()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    print("harness-ui on %s  (inference host: %s)" % (url, STATE.host or "this machine"), flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
