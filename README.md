# ai-inference-harness

Run **Qwen3.8-Flash-Next** (~180B parameters: a 125B MoE with 6B active, a 51B n-gram table and a 4B MTP head) on a **64 GB Apple Silicon Mac**, even though the model is larger than RAM. It is served over an **OpenAI-compatible API** so tools like Goose, opencode, or the OpenAI SDK can use it.

Measured on an M1 Ultra Mac Studio (64 GB):

| | Result |
|---|---|
| Decode speed | **~32 tok/s** on short prompts, **27 tok/s** at 123k context |
| Prefill | ~350 tok/s |
| GPU memory | 53.6 GB wired; the 28.8 GB n-gram table stays on SSD |
| Startup | 28 s cold, 4 s warm |

How it fits:
- **Metal:** transformer weights (47 GB, GSQ-RCO IQ3_XXS) are wired on the GPU.
- **mmap + lazy reads:** the n-gram table lives in its own GGUF shard on SSD, and only the rows each token needs are read.
- **MTP speculative decoding:** a small head drafts the next token and the main model verifies it, for +3–8% speed.
- **Unified KV cache:** quantized to q8_0, so every request can use the full 128k context.

Why and how, in full: [DESIGN.md](DESIGN.md), which includes benchmarks in Appendix A.

---

## Repository layout

| Folder | What it holds |
|---|---|
| [`inference/`](inference/) | **Backend.** Build script for llama.cpp at a pinned commit, model download script, `serve.sh` launcher, serving profiles |
| [`harness/`](harness/) | **Supervisor CLI** (Python stdlib only). Preflight checks, start/stop/status, logs, watchdog, chat, benchmarks, remote control over SSH |
| [`client/`](client/) | **Connecting over the OpenAI protocol.** curl and Python SDK examples, Goose provider config |
| [`harness-ui/`](harness-ui/) | **Mac app** (AI Harness.app, built from source): dashboard, settings, chat with web access and thinking effort, benchmarks, logs. See [harness-ui/README.md](harness-ui/README.md) |

Nothing built or downloaded is committed (see [.gitignore](.gitignore)). llama.cpp is fetched at the commit in [`inference/LLAMA_CPP_COMMIT`](inference/LLAMA_CPP_COMMIT), and models are downloaded to `~/models/`.

```
 Your laptop                                   Inference Mac (e.g. 192.168.1.49)
 ───────────                                   ─────────────────────────────────
 harness CLI ── SSH (rsync + commands) ──────▶ harness: preflight / backend / watchdog
                                                         │ starts inference/serve.sh
                                                         ▼
 harness chat ─┐                               llama-server (Metal) :8080
 Goose ────────┼── OpenAI API, HTTP :8080 ───▶   weights + MTP head → GPU
 any client ───┘                                 n-gram table → SSD (mmap)
```

---

## Requirements

**Inference machine (the big Mac):**
- Apple Silicon with 64 GB of memory, running macOS
- Xcode Command Line Tools (`xcode-select --install`)
- `git`
- [`uv`](https://docs.astral.sh/uv/), which is used to install `cmake`
- the Hugging Face CLI (`uv tool install huggingface_hub`)
- Python 3.9+ (the system Python is fine)
- about 90 GB of free disk space

**Controller (optional, e.g. your laptop):** Python 3.9+, `ssh` with key login to the inference machine, and `rsync`.

---

## 1. Set up and run the inference backend

Run these **on the inference machine**, from the repo root. They are shown here as `~/ai-harness`, which is also where the harness syncs to when you control the machine remotely.

```bash
git clone https://github.com/ilfir/ai-inference-harness.git ~/ai-harness
```

```bash
cd ~/ai-harness
```

**Build llama.cpp** (Metal, pinned commit, about 1 minute):

```bash
inference/build.sh
```

**Download the model** (~80 GB: 75.8 GB of weights plus the 4.1 GB MTP head; it resumes if interrupted):

```bash
inference/download.sh default
```

**Raise the GPU memory limit.** This is needed after every reboot:

```bash
sudo sysctl iogpu.wired_limit_mb=60416
```

**Start the server.** Pick one of two ways.

The plain shell script runs in the foreground, and Ctrl+C stops it:

```bash
inference/serve.sh default
```

Through the harness, the server runs in the background. The harness runs preflight checks first and waits until the server is healthy:

```bash
python3 -m harness serve default
```

The server listens on `http://0.0.0.0:8080`.

### Profiles

The profiles live in [`inference/profiles/`](inference/profiles/). List them with `python3 -m harness profiles`.

| Profile | Model | Context | MTP | GPU memory |
|---|---|---|---|---|
| `default` | Qwen3.8-Flash-Next IQ3_XXS | 128k | ✅ | ~54 GB |
| `chat` | same | 32k | ✅ | ~53 GB |
| `chat-nomtp` | same | 32k | – | ~50 GB (used as the watchdog's out-of-memory fallback) |
| `baseline-27b` | Qwen3.8-27B UD-Q4_K_XL | 32k | – | ~22 GB (for comparison) |

To add a model or change settings, copy a profile `.env` file and edit it. `serve.sh`, `download.sh` and the harness all read the same file. Flag details are in [`inference/README.md`](inference/README.md).

---

## 2. Use the harness

The harness is a command-line tool. Run it from the repo root with `python3 -m harness <command>`. It works in two ways:
- **On the inference machine:** run commands as written.
- **From another machine:** add `--host user@inference-mac`. The repo is rsynced over and the command runs there over SSH. `chat` and `bench` connect over HTTP instead.

```bash
H="--host ai@192.168.1.49"          # omit when running on the inference machine itself

python3 -m harness $H build                    # build llama.cpp
python3 -m harness $H download default         # download in the background
python3 -m harness $H download --status default
python3 -m harness $H preflight default        # check memory, GPU limit, model files, MTP head, port
python3 -m harness $H serve default            # start in the background, wait until healthy
python3 -m harness $H status                   # PID, health, speed, MTP acceptance, memory, swap
python3 -m harness $H logs -f                  # follow the server log
python3 -m harness $H chat                     # streaming chat in the terminal (/think, /nothink, /reset)
python3 -m harness $H bench                    # decode/prefill speed and MTP acceptance
python3 -m harness $H bench --fill 90000       # the same at ~125k context
python3 -m harness $H restart chat             # switch profile
python3 -m harness $H stop
```

**Keep it running automatically (optional).** This installs a user LaunchAgent watchdog. It needs no sudo:

```bash
python3 -m harness $H install-agent
```

What the watchdog does:
- It keeps the profile you last `serve`d running.
- It restarts the server after a crash (at most 3 times in 10 minutes).
- After a Metal out-of-memory crash, it switches to the profile's fallback.
- It warns if swap starts growing.

`stop` tells it to leave the server stopped. Remove it with `uninstall-agent`.

**Shell shortcut** (add to `~/.zshrc` on your laptop):

```bash
aih() { (cd ~/projects/ai-harness && python3 -m harness --host ai@192.168.1.49 "$@") }
```

Then run `aih status`, `aih chat`, `aih serve default`, and so on.

The full command reference is in [`harness/README.md`](harness/README.md). The architecture is described in [DESIGN.md §8–9](DESIGN.md).

---

## 3. Connect with the OpenAI protocol

| Setting | Value |
|---|---|
| Base URL | `http://<inference-mac>:8080/v1`, e.g. `http://192.168.1.49:8080/v1` |
| Model | `qwen3.8-flash-next` (`qwen3.8-27b` for the baseline profile) |
| API key | None by default (send any string). Set `API_KEY=...` in a profile to require one. |
| Endpoints | `/v1/chat/completions` (streaming, tool calls), `/v1/models`, `/health`, `/metrics` |

### curl

```bash
curl http://192.168.1.49:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "qwen3.8-flash-next",
  "messages": [{"role": "user", "content": "Hello!"}],
  "chat_template_kwargs": {"enable_thinking": false}
}'
```

More examples, including tool calls, are in [`client/examples/curl.sh`](client/examples/curl.sh).

### Python (official `openai` package)

```python
from openai import OpenAI

client = OpenAI(base_url="http://192.168.1.49:8080/v1", api_key="not-needed")
resp = client.chat.completions.create(
    model="qwen3.8-flash-next",
    messages=[{"role": "user", "content": "Hello!"}],
    temperature=0.7, top_p=0.8, presence_penalty=1.5,
    extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
)
print(resp.choices[0].message.content)
```

A streaming example that also shows thinking mode is in [`client/examples/openai_sdk.py`](client/examples/openai_sdk.py).

### Thinking vs non-thinking

Qwen3.8 **thinks by default**. The reasoning arrives in `reasoning_content`, separate from `content`. To turn it off, send `"chat_template_kwargs": {"enable_thinking": false}`. To think less, send `"chat_template_kwargs": {"reasoning_effort": "low"}` (or `"medium"`; the default is `"xhigh"`). A server-wide default can be set per profile with `REASONING_EFFORT` (and a hard cap with `REASONING_BUDGET`).

Recommended sampling, from the model card:

| Mode | Settings |
|---|---|
| Thinking | `temperature 1.0, top_p 0.95, top_k 20, min_p 0` |
| Non-thinking | `temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5` |

### Goose

1. **Install the provider.** The provider file is [`client/goose/qwen_flash_next.json`](client/goose/qwen_flash_next.json). Install it on the machine that runs Goose:

   ```bash
   python3 -m harness --host ai@192.168.1.49 goose-config --install
   ```

2. **Restart Goose** fully (⌘Q, then reopen it).
3. **Select the model:** choose **Qwen3.8-Flash-Next (Studio)** under **Settings → Models**.

Details are in [`client/goose/README.md`](client/goose/README.md).

### Tips for agent tools

- **The first turn is slow.** It takes about 25–30 s, because the server reads the tool's system prompt and tool definitions (~8.5k tokens). Later turns reuse the prompt cache and start in about 1 s. Set the client timeout to around 600 s.
- **Context limit:** set the tool's context limit to the profile's `CTX` (131072 for `default`).
- **Concurrency:** up to 2 parallel requests are served. Each runs slower (~15–25 tok/s) while both are active.
- **Memory:** don't run another large model server on the same Mac at the same time. There isn't memory for both.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Preflight reports `GPU wired limit` ❌ | Run the `sudo sysctl iogpu.wired_limit_mb=…` command it prints. The setting resets on reboot. |
| Preflight reports `free memory` ❌ | Stop other model servers (Ollama, LM Studio, Unsloth, MLX), or use `--force` if you know the numbers. |
| The server dies on its first reply with a Metal `OutOfMemory` error | Use the `chat` or `chat-nomtp` profile, and check that the n-gram table is in its own shard (preflight shows this). |
| Slow first response in every Goose turn | The tool must send identical prompt prefixes for the cache to hit. Clients that rewrite earlier messages force a full re-read. |
| Swap grows during long agent sessions | Lower `CACHE_RAM_MB` in the profile (default 2048 MiB), which caps llama-server's host-RAM prompt cache. |
| GitHub is slow from the inference Mac | Copy a llama.cpp checkout of the pinned commit into `~/ai-harness/llama.cpp`; `build.sh` skips the fetch when it finds that commit. |
