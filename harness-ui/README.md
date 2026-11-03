# harness-ui — AI Harness for Mac

A desktop app for running and using the inference backend: start and stop it, change its settings, watch speed
and memory, chat with the model (with web access), run benchmarks and read the logs. The CLI in `harness/` is
unchanged; the app drives the same code.

## Build and run

```bash
harness-ui/macos/build-app.sh            # -> build/AI Harness.app
harness-ui/macos/build-app.sh --install  # also installs it to /Applications (asks before removing old copies)
```

`--install` always installs to `/Applications`. If a version is already installed in `/Applications` or
`~/Applications`, it lists them and asks before removing them: type `y` to quit the running app, delete those copies
and install the new one. Any other answer cancels the install and leaves the old copies in place (the new build stays in
`build/AI Harness.app`).

Needs the Xcode Command Line Tools (`swiftc`, `/usr/bin/python3`, `iconutil`). Nothing prebuilt is kept in the repo:
the build compiles `macos/main.swift`, draws the icon with `macos/icon.swift` and bundles `harness-ui/` together
with `harness/`, `inference/` and `client/`.

On first launch, open **Settings → Connection** and enter the inference host's SSH target (for example
`ai@192.168.1.49`) and its harness home (`~/ai-harness`). The app needs key-based SSH to that host, like the CLI's
`--host` mode. Leave the host empty when the app runs on the inference machine itself. Connection settings are
stored in `~/Library/Application Support/AI Harness/settings.json`, chat history in `chats.json` next to it.

Without the app wrapper, the same UI runs in a browser:

```bash
python3 harness-ui/server.py --host ai@192.168.1.49   # opens http://127.0.0.1:8765
```

## Tabs

The app opens on the Chat tab.

| Tab | What it does |
|---|---|
| Dashboard | Start / Restart / Stop a profile (with preflight), backend status, decode speed and MTP acceptance since start, memory (wired, compressed, swap, GPU wired limit), watchdog install / remove. |
| Settings | Per-profile settings (below) and the connection. |
| Chat | Streaming chat with thinking effort, web access, sampling sliders and a system prompt. Shows tokens, tok/s, prompt time and MTP acceptance per reply. Chat history on the left (below). |
| Benchmark | The CLI's `bench` with optional context fill (8k to 125k tokens). |
| Logs | Backend or watchdog log with filter and auto-refresh. |

## Chat history

Every chat is saved as you go: after each question and again when the reply finishes (including its thinking,
web tool calls and stats line). The **History** column on the Chat tab lists them, most recently used first.

- **+ New chat** starts an empty chat. It is added to the history once you send its first message, titled by that
  message.
- Click a chat to reopen it and keep going; the model gets the whole earlier conversation as context. Chats
  cannot be switched while a reply is streaming (press **Stop** first).
- **×** on a chat deletes it after a confirmation.
- At most **20** chats are kept. When a 21st is saved, the least recently used one is removed.

Sampling parameters and the system prompt are not stored per chat: the current values apply to whatever you
send next. History is kept on this Mac, never on the inference host: in the app at
`~/Library/Application Support/AI Harness/chats.json` (mode 600). With `server.py` run from the repo, it is
`harness-ui/chats.json` (gitignored), or wherever `AIH_UI_CHATS` points.

## Settings

Saved on the inference host as per-machine overrides in `inference/profiles/<profile>.local.env` (mode 600, never
committed or overwritten by sync). `serve.sh` and the CLI read them too. Restart the backend to apply; the
Dashboard shows a banner when the running profile's settings changed.

| Setting | Notes |
|---|---|
| Context size | 8k to 256k tokens. KV memory grows with it. |
| Parallel requests | Slots; they share the GPU. A long prompt on one slot slows replies on the others. |
| MTP / draft length | Speculative decoding with the MTP head. Draft length 1 measured best. |
| Default thinking effort | Server-wide `reasoning_effort` (low / medium / xhigh) or off, for clients like Goose. |
| Thinking token budget | `--reasoning-budget`: hard cap on thinking tokens; -1 unlimited. |
| KV cache type, prompt cache RAM, CPU threads, port, listen address, GPU budget | As in the profile files. |
| API key | Clients must send it as a bearer token. The app adds it to its own requests. |
| Extra llama-server arguments | Appended verbatim. |

**Reset profile to defaults** deletes the overrides file.

## Thinking effort

Qwen3.8's chat template reads `reasoning_effort` (`low`, `medium`, `xhigh`; `high` means `xhigh`, the default) and
`enable_thinking`. The Chat tab sends them per message. Other clients can do the same:

```json
"chat_template_kwargs": {"reasoning_effort": "low"}
```

## Web access

With **Web access** ticked, the app gives the model two tools and runs them on this Mac:

- `web_search`: DuckDuckGo results (title, URL, snippet). No API key needed.
- `fetch_url`: the readable text of a page, truncated to 12,000 characters. Private, loopback and link-local
  addresses are refused, so pages cannot steer the model into the local network.

The model may search and read several pages before it answers (up to 8 tool rounds). Each call shows in the chat.
Fetched pages add thousands of prompt tokens, so web answers take longer than plain ones.

## How it works

```
AI Harness.app (Swift, WKWebView)
  └─ server.py on 127.0.0.1:<free port>       UI, API, web tools
       ├─ chats.json (local)                   chat history, max 20 (chats.py)
       ├─ ssh <host> python3 - <op> <json>     ops.py: status, profiles, start/stop, logs, settings
       ├─ rsync inference/ harness/ client/    before starts (same as `harness sync`)
       └─ HTTP → llama-server :8080            chat (SSE) and benchmarks
```

`ops.py` is piped over SSH, so the host needs nothing beyond the synced `harness/` package. One SSH connection is
reused for the status polls (ControlMaster). The server only listens on localhost and refuses cross-origin POSTs.
