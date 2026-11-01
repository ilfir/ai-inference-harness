# harness/

The harness is a supervisor and CLI for the inference backend. It uses only the Python standard library, so it runs on 3.9+ with no installs. Run it from the repo root as `python3 -m harness`.

## Commands

| Command | Does |
|---|---|
| `profiles` | Lists the profiles in `inference/profiles/` |
| `sync` | rsyncs `inference/`, `harness/` and `client/` to `--host`. Remote commands do this automatically unless you pass `--no-sync`. |
| `build [--commit] [--clean]` | Runs `inference/build.sh` |
| `download [profile] [--status] [--foreground]` | Runs `inference/download.sh`. By default it runs in the background, logging to `logs/download-<profile>.log`. |
| `preflight [profile]` | Checks that the binary supports `draft-mtp` and that all shards are present. Also checks that the n-gram table sits alone in its shard, that the MTP head arch matches, the wired GPU limit, free memory, competing large processes, the port and disk space. |
| `serve [profile] [--force] [--no-wait]` | Runs preflight, then starts `inference/serve.sh` detached and waits for `/health` |
| `stop`, `restart [profile]` | Stops or restarts the backend. `stop` also tells the watchdog to leave it stopped. |
| `status` | Shows the backend PID and health, the latest decode speed and MTP acceptance from the log, memory, swap, the wired limit and the watchdog heartbeat |
| `logs [-f] [-n N] [--watchdog]` | Shows the backend or watchdog log |
| `chat [profile] [--no-think] [--system]` | Streaming REPL over the OpenAI API, using the Qwen-recommended samplers. Thinking text is shown dimmed. |
| `bench [profile] [--fill N] [--max-tokens] [--temperature] [--runs] [--out]` | Measures prefill and decode speed, TTFT and MTP acceptance, then prints a markdown table. A random nonce defeats the prompt cache. |
| `watch` | The watchdog loop: keeps the desired profile running, restarts it after a crash (at most 3 times in 10 min), switches to `FALLBACK_PROFILE` after a Metal OOM and warns when swap grows |
| `install-agent`, `uninstall-agent` | Installs or removes the watchdog as a user LaunchAgent (`~/Library/LaunchAgents/ai.harness.watchdog.plist`). No sudo is needed. |
| `goose-config [--install]` | Prints the Goose provider, or installs it on **this** machine. It never overwrites an existing provider. |

The global `--host user@host` runs commands on that host over SSH. The exceptions are `chat` and `bench`, which connect to `http://host:PORT` directly, and `goose-config`, which always acts on the local machine. The runtime home on the host is `--remote-home`, `~/ai-harness` by default.

## Layout

| Module | Role |
|---|---|
| `config.py` | Repo and runtime paths (`AIH_HOME`) and the profile `.env` loader |
| `gguf.py` | GGUF header reader: metadata and tensor list, without loading any weights |
| `system.py` | macOS probes: `vm_stat`, swap, `iogpu.wired_limit_mb`, large processes, ports |
| `preflight.py` | The checks run before launch |
| `backend.py` | Process lifecycle and state in `run/backend.json` and `run/desired.json` |
| `watchdog.py`, `agent.py`, `launchd/` | Supervision under launchd |
| `remote.py` | rsync + ssh |
| `client.py`, `bench.py` | Minimal OpenAI client and the benchmark |

Runtime state (`logs/`, `run/`, `slots/`) lives under `AIH_HOME` and is git-ignored.
