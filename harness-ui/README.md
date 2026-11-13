# harness-ui — AI Harness for Mac

A desktop app for running and using the inference backend: start and stop it, change its settings, watch speed
and memory, chat with the model (with web access), run benchmarks and read the logs. The CLI in `harness/` is
unchanged; the app drives the same code.

The app knows two engines and uses whichever is serving the API:

| Engine | Profiles | Started through |
|---|---|---|
| **llama.cpp** (the harness backend) | `inference/profiles/*.env` | `harness.backend`, as the CLI does |
| **Strata** (prepared on the host by the ai-inference-strata trial, a sibling project) | `harness-ui/strata-profiles/*.env` | the trial's own `scripts/strata-start.sh` / `strata-stop.sh` |

Only one engine can hold the GPU, so **Restart** with a profile of the other engine stops the running one first.
Chat, Benchmark, status and logs work the same against either; a Strata started outside the app (for example by
the trial's `strata-backend.sh`) is detected too and shown as profile `external`. See [Strata](#strata) below.

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
`ai@192.168.1.49`), its harness home (`~/ai-harness`) and, if Strata is set up there, its Strata home (the trial
directory, default `~/ai-inference-strata`). The app needs key-based SSH to that host, like the CLI's `--host` mode.
Leave the host empty when the app runs on the inference machine itself. Connection settings are stored in
`~/Library/Application Support/AI Harness/settings.json`, chat history in `chats.json` and Strata profile
overrides in `strata-overrides.json` next to it.

Without the app wrapper, the same UI runs in a browser:

```bash
python3 harness-ui/server.py --host ai@192.168.1.49   # opens http://127.0.0.1:8765
```

## Tabs

The app opens on the Chat tab.

| Tab | What it does |
|---|---|
| Dashboard | Start / Restart / Stop a profile of either engine (with preflight), backend status with the engine and its running flags, decode speed and draft acceptance since start, memory (wired, compressed, swap, GPU wired limit), watchdog install / remove. |
| Settings | Per-profile settings (below, for both engines) and the connection. |
| Chat | Streaming chat with thinking effort, web access, folder access, sampling sliders and a system prompt. Replies render as Markdown (headings, lists, tables, code blocks with a copy button, links that open in the browser, images). Shows tokens, tok/s, prompt time and MTP acceptance per reply. Chat history on the left (below). Drag the dividers to resize the history and parameter panels (double-click to reset; sizes are remembered and the layout refits when the window shrinks). |
| Benchmark | The CLI's `bench` with optional context fill (8k to 125k tokens). |
| Logs | The running backend's log, the llama.cpp server log, the Strata server or engine log, or the watchdog log, with filter and auto-refresh. |

## Chat history

Every chat is saved as you go: after each question and again when the reply finishes (including its thinking,
web tool calls and stats line). The **History** column on the Chat tab lists them, most recently used first.

- **+ New chat** starts an empty chat. It is added to the history once you send its first message, titled by that
  message.
- Click a chat to reopen it and keep going; the model gets the whole earlier conversation as context. You can
  switch chats or start a new one while a reply is streaming: it keeps going in the background (marked
  "replying" in the list) and is there when you come back. The backend answers one request at a time, so Send
  in another chat waits until that reply finishes or you press **Stop**.
- **×** on a chat deletes it after a confirmation.
- At most **20** chats are kept. When a 21st is saved, the least recently used one is removed.

The folder a chat may read (below) is saved with the chat. Sampling parameters and the system prompt are not:
the current values apply to whatever you send next. History is kept on this Mac, never on the inference host: in the app at
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

## Strata

The Strata profiles are the configurations the trial measured (its REPORT.md has the numbers):

| Profile | Config | Measured on the M1 Ultra |
|---|---|---|
| `strata-resident` | weights resident on the GPU, no MTP, 1 slot, 128k (the trial's R-A, what `strata-backend.sh` runs) | 1.34–1.50x llama.cpp decode (44 tok/s short, 42 tok/s at 59k), ~59 GB wired, no swap growth in the soak |
| `strata-resident-draft` | resident, MTP layer + prompt-lookup drafter, 2 slots (R-C) | up to 1.62x at 59k, but 61 GB wired and ~0.9 GB of swap growth; needs `prepare.sh --download-mtp` |
| `strata-stream` | experts streamed from the SSD, no MTP, 2 slots (S-A, what Strata's planner picks on 64 GB) | 0.49–0.55x llama.cpp, ~35 GB of memory |

Each key in a Strata profile is an environment knob of the trial's `scripts/strata-start.sh` (read by its
`make-config.py`): `CTX`, `PARALLEL`, `MTP`, `SPEC`, `SUFFIX_DRAFT`, `STREAM`, `KV_TYPE`, `CONV_CACHE_MIB`,
`BATCH_MTP`, `PORT`, `HOST`, `STRATA_API_KEY`, `EXTRA_ARGS`, plus `GPU_BUDGET_GB` for the app's preflight. Nothing
on the host reads these files: the app sends the effective environment with every start, so overrides are kept on
this Mac (`strata-overrides.json`, mode 600). The Settings tab edits them like the llama.cpp ones.

What the app does for a Strata profile:

- **Preflight**: the Strata directory, start script, engine binary, model pack and (with MTP on) the packed MTP
  layer exist; the trial's `make-config.py` accepts the knobs (run to a scratch file, so a running Strata's config is
  untouched); GPU wired limit, free memory, large processes, port, disk.
- **Start**: runs `strata-start.sh` detached with the profile's environment (`ALLOW_OPEN=1` is added when listening
  on the LAN without a key, the same exposure as the llama.cpp profiles) and returns once Strata's pid file exists;
  the Dashboard then polls until the model is loaded. The harness watchdog's wish to run llama.cpp is cleared first
  so it cannot start llama-server beside Strata. **Skip preflight** passes `FORCE=1` to the script.
- **Stop**: runs `strata-stop.sh` (SIGTERM, then SIGKILL after 60 s, leftover engine processes included).
- **Status**: Strata's pid file and run config (`config/strata-iq3xxs.json`) give the process, port and flags;
  `/health`, `/slots` and `/metrics` give health, busy slots, totals since start and the last request.
- **Chat and Benchmark**: unchanged; Strata answers on the same OpenAI API with `reasoning_content` and
  llama-style `timings`. The API key, if any, is read from the run config of the Strata that is actually running.

Strata's resident weights are file-backed mappings, so when it is idle macOS counts them as reclaimable rather
than wired; the Memory card shows what `vm_stat` reports. To add a Strata configuration, copy a profile file in
`harness-ui/strata-profiles/` and rebuild the app (the files are bundled with it).

## Thinking effort

Qwen3.8's chat template reads `reasoning_effort` (`low`, `medium`, `xhigh`; `high` means `xhigh`, the default) and
`enable_thinking`. The Chat tab sends them per message. Other clients can do the same:

```json
"chat_template_kwargs": {"reasoning_effort": "low"}
```

## Web access

With **Web access** ticked, the app gives the model three tools and runs them on this Mac:

- `web_search`: DuckDuckGo results (title, URL, snippet). No API key needed.
- `image_search`: DuckDuckGo image results (image URL, thumbnail, source page). The model shows them inline with
  Markdown image syntax, so "show me a picture of X" works with web access on. Images load from their original
  host (no referrer is sent); a click opens the full image in the browser.
- `fetch_url`: the readable text of a page, truncated to 12,000 characters. Private, loopback and link-local
  addresses are refused, so pages cannot steer the model into the local network.

The model may search and read several pages before it answers (up to 8 tool rounds). Each call shows in the chat.
Fetched pages add thousands of prompt tokens, so web answers take longer than plain ones.

## Skills

The **Skills** checkbox (Chat tab) lets the model use skills the way Goose does: folders containing a `SKILL.md`
(front matter `name` and `description`) and optional scripts. The app scans `~/.agents/skills`, `~/.claude/skills`
and `~/.config/goose/skills` (plus any colon-separated folders in `HARNESS_SKILLS_DIRS`) on every message, so
**adding a skill is dropping its folder into one of those places**; the checkbox's note lists what was found.

With it on, the system prompt lists each skill's name and description and the model gets two tools, run on this Mac:

- `load_skill`: the full `SKILL.md` instructions and the files that ship with the skill.
- `run_skill_script`: runs a `.py` or `.sh` script inside the skill folder with a list of arguments (no shell, 120 s
  timeout, 12,000 characters of output). Python goes through `uv run` when `uv` is installed, so a script's declared
  dependencies are installed on first use; otherwise `python3`. The script path is resolved inside the skill folder.

Skills are code that runs with your user's rights, so only install ones you trust. The first one wired up is
`youtube-transcript`: paste a YouTube link and ask for a summary or the transcript.

## Folder access

A chat can be given one folder on this Mac that the model may read. On the Chat tab, the folder bar above the message box: **Choose
folder…** opens the Mac folder panel (in a browser, the macOS dialog; elsewhere a path can be typed). The
folder is saved with the chat and shown as 📁 in the history list; **Change…** picks another one and **Remove**
takes it away. New chats start with no folder.

With a folder set, the model gets three tools that run in the app's server on this Mac:

- `list_files`: the entries of a directory (optionally recursive), with sizes.
- `search_files`: lines containing some text across the folder (case-insensitive), optionally limited to a
  subdirectory or a name pattern such as `*.md`. Up to 60 matches.
- `read_file`: a text file, 12,000 characters at a time; longer files say which line to continue from.

Every path the model passes is resolved under the chat's folder, so nothing outside it can be listed or read:
`..`, absolute paths and symlinks that lead out are refused. Hidden entries (names starting with `.`) and build
folders such as `node_modules`, `build` and `__pycache__` are skipped, binary files are not read, and files
over 4 MB are listed but not read or searched. The system prompt tells the model to treat file contents as data,
never as instructions. Web access and folder access can be on at the same time; the same 8-round limit covers
both.

The first time the app reads from Desktop, Documents, Downloads or an external volume, macOS asks whether
AI Harness may access that location, as it does for any app.

## How it works

```
AI Harness.app (Swift, WKWebView)
  └─ server.py on 127.0.0.1:<free port>       UI, API, web and file tools (webtools.py, filetools.py)
       ├─ chats.json (local)                   chat history with each chat's folder, max 20 (chats.py)
       ├─ strata-profiles/ + strata-overrides.json   Strata profiles and this Mac's overrides (strata.py)
       ├─ ssh <host> python3 - <op> <json>     ops.py: status, profiles, start/stop, logs, settings
       │     ├─ harness.backend                llama-server (inference/serve.sh)
       │     └─ <strata home>/scripts/strata-start.sh | strata-stop.sh   Strata, with the profile's env
       ├─ rsync inference/ harness/ client/    before starts (same as `harness sync`)
       └─ HTTP → :8080 (llama-server or Strata)   chat (SSE) and benchmarks
```

`ops.py` is piped over SSH, so the host needs nothing beyond the synced `harness/` package (and, for Strata, the
trial directory, which the app never modifies). One SSH connection is reused for the status polls (ControlMaster).
The server only listens on localhost and refuses cross-origin POSTs.
