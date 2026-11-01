# AI Harness — Design: Qwen3.8-Flash-Next on a 64 GB M1 Ultra

Status: **Draft for review** · 2026-10-31

## 1. Goal

Run **Qwen3.8-Flash-Next** (~180B total parameters) on a Mac Studio whose memory is smaller than the model, using:
- **mmap**, so the parts of the model that don't fit stay on SSD
- **MTP** (multi-token prediction) speculative decoding
- **NUMA-aware tuning**, where the platform allows it
- other current techniques

The harness should be separate from the inference backend, with a clean API boundary between them.

## 2. Target machine (measured over SSH, `ai@192.168.1.49`)

| Item | Value |
|---|---|
| SoC | Apple M1 Ultra (2× M1 Max dies over UltraFusion) |
| CPU | 20 cores (16 P + 4 E) |
| GPU | 48 cores, Metal 4 |
| Memory | 64 GB unified (~800 GB/s) |
| GPU wired limit | `iogpu.wired_limit_mb = 60416` (already raised from the default) |
| Disk | Internal Apple Fabric SSD, 926 GB total, **369 GB free** |
| OS | macOS 26.5 |
| Toolchain | Apple clang 21, Xcode CLT only (no `metal` CLI), **no cmake / brew** |
| Python | System 3.9.6; `uv`, `hf` (huggingface_hub 2.1.1) in `~/.local/bin` |
| sudo | Needs a password; non-interactive sudo is not possible |
| Network to HF | ~10.7 MB/s on a single stream, so ~2.2 h per 85 GB |
| Existing | `~/llama.cpp` (master from Oct 8, too old), `~/.unsloth/llama.cpp`, other inference projects. **None of these get touched.** |
| Note | At check time, `rpcsvchost` (root) held about 15 GB resident, with 48 GB free. This needs watching, see §10. |

## 3. Model facts that drive the design

From `Qwen/Qwen3.8-Flash-Next` `config.json` and its README, plus the GGUF headers:

- **125B MoE with 6B activated**, plus a **51B n-gram embedding table** and a **4B MTP layer**.
- Architecture `qwen4exp`, 48 layers in a hybrid layout: 3 Gated DeltaNet layers followed by 1 Qwen Sparse Attention (QSA) layer, repeated. Hyper-connections are 4 streams wide.
- MoE: 512 experts, 10 routed plus 1 shared. Expert FFN width is 640.
- Attention: only **12 of the 48 layers keep a KV cache**, each with 2 KV heads of dimension 256. QSA reads a top-2048-token budget per query. **KV is tiny**: about 24 KB/token at f16, so 32k context is about 0.8 GB.
- DeltaNet recurrent state: about 115 MB per sequence, fixed size.
- **N-gram table (`per_layer_token_embd`)**: [160 × 320M] rows. Each token reads about 16 rows (~2.7 KB) at hashed addresses. This is a lookup table, not a matmul, so it pages from SSD well. Experts are the opposite: about 6B parameters are touched per token, gigabytes of reads, far too much to stream from disk.
- Native context is 262k.

**Key insight:** the model only "doesn't fit" because of the 51B n-gram table. The working set (trunk plus experts at ~4 bits) is 46–56 GB, which fits in 64 GB. The table (38–54 GB) lives on SSD behind mmap, and the hot n-grams end up in the page cache.

## 4. Backend choice: `llama.cpp` (`llama-server`, Metal)

| Option | Verdict |
|---|---|
| **llama.cpp master** | ✅ Has the `qwen4exp` arch, a `qwen4exp` MTP graph (`LLM_GRAPH_TYPE_DECODER_MTP`), `--spec-type draft-mtp`, mmap-by-default, Metal, an OpenAI-compatible server, and slot save/restore. |
| MLX / mlx-lm | ❌ Experts and table would need custom work to avoid loading everything; there are oQ4e-mtp community quants, but no SSD-backed table path. Revisit later. |
| vLLM / SGLang / KTransformers | ❌ CUDA-oriented; not viable on macOS. |
| unsloth llama.cpp fork | Fallback only. It adds "shared" MTP heads, which borrow the embedding and output from the main model and save about 1.3 GB. |

Build a **pinned commit** of `ggml-org/llama.cpp` master into `~/ai-harness/llama.cpp`:
```
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON \
      -DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF
cmake --build build -j 20 --target llama-server llama-cli llama-bench llama-gguf-split
```
cmake comes from `uv tool install cmake`, so no brew or sudo is needed.

Verified in source (master `0ec4027`, 2026-10-30): `src/models/qwen4exp.cpp` loads the nextn block and builds `graph_mtp`. `common/speculative.cpp` implements `draft-mtp`. Tensor borrowing (`borrow_shared_tensor`) is **not** in mainline, so we must use a **self-contained** MTP head.

## 5. Model artifacts

### 5.1 Main weights: `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`, **IQ3_XXS** (chosen 2026-10-31)
A non-uniform 3.0 bpw quant made with GSQ + RCO. It comes as 2 shards:
- **Shard 1 (47.04 GB):** all transformer weights. This shard goes on the GPU.
- **Shard 2 (28.80 GB):** **only** `per_layer_token_embd`, the n-gram table, stored as IQ4_NL. It stays mmap'd on SSD.

I checked both headers. Shard 1 has blk 0–47 and no nextn/MTP tensors. Shard 2 has the single table tensor and nothing else.

Keeping the table in its own shard matters because llama.cpp wires (Metal-maps) any whole shard that contains a GPU tensor. If the table shared a shard with GPU weights, it would be wired too, and the first decode would fail with `kIOGPUCommandBufferCallbackErrorOutOfMemory`.

ISTA's published quality vs BF16 (task average over AIME25, GPQA-D, and LiveCodeBench v6):

| Build | Resident | Table on SSD | Task avg (BF16 = 93.12) | LCB v6 Δ |
|---|---|---|---|---|
| IQ3_S | 54.8 GB | 28.8 GB | 93.26 (≈100%) | −0.6 |
| **IQ3_XXS (chosen)** | **47.0 GB** | 28.8 GB | **92.57 (99.4%)** | −1.1 |
| Q2_0 (speed alternative) | 37.6 GB | 28.8 GB | 89.07 (95.7%) | −6.3 |

Alternatives that were considered and dropped:
- **AtomicChat M64 builds.** IQ4_XS has a top-1 match of only 82.7%. Q4_K_M has 56.1 GB resident, which leaves no room for MTP.
- **unsloth UD-IQ1/IQ2.** No quality numbers are published, and the table is not isolated in its own shard.

### 5.2 MTP head: `unsloth/Qwen3.8-Flash-Next-GGUF/MTP/`
- `mtp-Qwen3.8-Flash-Next-Q8_0.gguf` (3.85 GB, self-contained) is the primary choice.
- `mtp-…-Q4_K_M.gguf` (2.6 GB) is the fallback if memory is tight. It accepts about 2 points fewer drafts.
- unsloth measured about **66% acceptance** and a **1.3–1.7× speedup** at batch 1 with greedy decoding (on a B200). The gain is lower at temperature 1.0, and MTP is a **net loss at concurrency ≥ 8**.

## 6. Memory budget and profiles

Wired limit is 60.4 GB. Keep about 4 GB for macOS and services, and leave the rest to page cache for the table.

All profiles use the same IQ3_XXS weights plus the MTP Q8_0 head. They differ only in context length and speculation settings.

| Component | `default` (128k) | `chat` (32k) |
|---|---|---|
| Main weights (wired) | 47.0 GB | 47.0 GB |
| MTP head | 3.85 GB (Q8_0) | 3.85 GB (Q8_0) |
| KV cache, q8_0 | ~1.6 GB | ~0.5 GB |
| DeltaNet state + compute buffers | ~2 GB | ~1.5 GB |
| **GPU total** | **~54.5 GB** | **~53 GB** |
| Left for OS + table page cache | ~9 GB | ~11 GB |
| Speculation | `draft-mtp` (n-max 1, measured best) | same |

Estimated decode speed on the M1 Ultra: about 26 tok/s without MTP and about 34 tok/s with MTP at 128k. These figures are **estimates (±30%)** until §11 benchmarks them. Prefilling a full 128k prompt will take roughly 7–10 minutes, after which prompt caching reuses it.

**Decision (2026-10-31):** use ISTA IQ3_XXS + MTP. The download is in progress.

## 7. Techniques applied

| Technique | How | Notes |
|---|---|---|
| **mmap / SSD offload** | Keep the default mmap. Never use `--no-mmap` or `--mlock`. The table shard is never offloaded to the GPU, so it stays pageable. | About 3 MB/s of random reads at 36 tok/s, far below what the SSD can deliver. |
| **Shard-isolated table** | Use only builds that put the table in a shard of its own. The harness preflight **verifies** that the last shard holds only `per_layer_token_embd`. | This guards against wiring the table and hitting OOM. |
| **MTP speculative decoding** | `-md <mtp.gguf> --spec-type draft-mtp --spec-draft-n-max 1 -ngld 99` | Measured +3–8% at n-max 1; n-max 2 was a net loss on the M1 Ultra (Appendix A). |
| **N-gram self-speculation** | `--spec-type ngram-mod` (or combined with draft-mtp) | Free speedup for code edits and repeated text. |
| **GPU wired limit** | Check `iogpu.wired_limit_mb ≥ 57344`. The harness **prints** the `sudo sysctl` command and never runs sudo itself. | The setting resets on reboot. Optionally, a LaunchDaemon plist could persist it, but that is for you to install. |
| **Flash attention + quantized KV** | `-fa on -ctk q8_0 -ctv q8_0` | Halves KV memory with negligible quality loss. |
| **Disable auto-fit** | `-fit off`, with explicit `-c` / `-ngl 99` | AtomicChat reports that auto-fit mis-sizes this arch. ISTA recommends `-lm mmap --lazy-mode on`; check whether the pinned build supports it. |
| **Prompt prefix caching** | On by default; `--slot-save-path` for persistence | Requests that start with the same tokens as a slot's cache only prefill the new part (measured: 8.5k-token Goose prompt → 82 new tokens, 26 s → 1.3 s). `--cache-reuse` (KV shifting) is **not supported** for this model: llama.cpp disables it because DeltaNet state can't be shifted, so a client that rewrites earlier messages forces a full re-prefill. |
| **Table page-cache prewarm** (optional) | Sequential `F_RDADVISE`/read of the hot table regions after startup | Only if benchmarks show cold-start latency. Measure before building it. |
| **Thread / QoS tuning** | `-t 16 -tb 16` (P-cores), launched with user-interactive QoS | The CPU does very little here because everything runs on the GPU. |
| **NUMA** | See below. | |

### NUMA, honestly
The M1 Ultra is physically two dies, but **macOS exposes no NUMA topology or API**: no libnuma, no thread-to-die pinning, no memory-node binding. llama.cpp's `--numa` is Linux-only (it reads `/sys`) and does nothing on macOS. The GPU spreads work across both dies over UltraFusion (~2.5 TB/s) on its own. **What we do instead:** put all layers on Metal (`-ngl 99`), avoid CPU expert offload (which would bounce data between dies), and run 16 P-core threads for the small CPU portion. The design will not claim a NUMA optimization the OS can't deliver.

### Considered and rejected
- **Streaming experts from SSD** (`--override-tensor exps=CPU` with mmap): each token touches GBs of expert data, so expect under 2 tok/s. This is only a fallback for Q5 builds.
- **RPC split across a second machine**: possible later via `ggml-rpc`, but out of scope.

## 8. Architecture

```
┌──────────── Your Mac (M3, 8 GB) ────────────┐        ┌──────────────────── Mac Studio (192.168.1.49) ─────────────────────┐
│                                             │  SSH   │                                                                    │
│  harness CLI  (python3 -m harness           │ ─────▶ │  harness agent (stdlib)          backend: llama-server (Metal)      │
│               --host ai@192.168.1.49)       │        │  ┌────────────────────┐ spawn   ┌─────────────────────────────┐    │
│   • setup / download / serve / stop         │        │  │ • preflight        │ ──────▶ │ IQ3_XXS shard 1 → GPU       │    │
│   • status / bench / chat                   │        │  │ • start/stop/pid   │         │ shard 2 (n-gram) → mmap/SSD │    │
│                                             │  HTTP  │  │ • watchdog (launchd│ ◀─────▶ │ MTP head → GPU              │    │
│                                             │ ─────▶ │  │   KeepAlive)       │ :8080   │ OpenAI API /v1/*  /health   │    │
│  Goose.app  ── provider "qwen_flash_next" ──┼──────▶ │  └────────────────────┘         └─────────────────────────────┘    │
│                                             │ :8080  │                                                                    │
└─────────────────────────────────────────────┘        └────────────────────────────────────────────────────────────────────┘
        other LAN clients (Open WebUI, opencode, curl) ── OpenAI-compatible HTTP :8080
```

**Separate processes.** `llama-server` is the backend. The harness is a thin supervisor and client that sits outside the inference path:
- A harness crash never takes the model down.
- A backend restart reloads about 50 GB from SSD, roughly 30–60 s.
- Clients speak the plain OpenAI API directly to the backend, so any tool works: Goose, Open WebUI, opencode, curl.

### 8.1 Deployment modes

The same code runs in both modes. `--host` selects which one.

| Mode | Command runs on | How it reaches the Studio | Use for |
|---|---|---|---|
| **Remote** (default for you) | Your Mac | Over SSH (`ai@192.168.1.49`, key auth already works) for setup, preflight, serve/stop and watchdog control. Over HTTP to `:8080` for status, bench and chat. | Day-to-day control from your laptop |
| Local | The Studio itself | Direct subprocess and `localhost:8080` | Running on the Studio itself |

How remote mode works:
- The CLI copies the `harness/` package to `~/ai-harness/` on the Studio once, using `rsync` over SSH, then runs `ssh ai@… python3 -m harness agent <cmd>`.
- Each command returns JSON, which the local CLI prints.
- There is no daemon or open port beyond `llama-server` itself.

Splitting the work this way keeps the backend independent of your Mac:
- **The watchdog runs on the Studio** as a user LaunchAgent (`~/Library/LaunchAgents/ai.harness.backend.plist`, `KeepAlive`). It keeps the backend up and restarts it after a crash or an OOM fallback, even while your Mac is asleep, off, or off the network. No sudo is needed.
- **Your Mac is only a remote control.** Closing the laptop doesn't stop a running generation, including Goose sessions from other machines.
- **Downloads and builds run on the Studio** (`nohup`), with logs in `~/ai-harness/logs/`. The CLI tails them over SSH.

Remote-mode requirements:
- SSH key access, which already works.
- `rsync`, which is on macOS by default.
- Python 3.9+ on both ends. The code is stdlib only, because the Studio's system Python is 3.9.6.

### 8.2 Client integration: Goose (and other OpenAI-compatible tools)

The backend exposes `http://192.168.1.49:8080/v1`. That is OpenAI chat completions with streaming and tool calling (`--jinja` applies the model's own tool-call template). It also runs with `--alias qwen3.8-flash-next`, so clients see a stable model name.

**Goose.** You already use a custom provider for the Unsloth server on port 8888. The harness ships a second provider file at `client/goose/qwen_flash_next.json`; installing it means copying it to `~/.config/goose/custom_providers/` and selecting **"Qwen3.8-Flash-Next (Studio)"** in Goose. See [client/goose/README.md](client/goose/README.md).

Tuning for agent clients:

| Concern | Setting |
|---|---|
| Large tool schemas resent on every turn | Prefix caching: after the first turn only the new tokens are prefilled. Measured with Goose, the first turn read 8.5k tokens in 26 s, and the next turn read 82 new tokens in 1.3 s. |
| Port clash with Unsloth (`:8888`) | The backend uses `:8080`. Preflight warns if Unsloth, Ollama or LM Studio has a model loaded, because there isn't memory for both. |
| Parallel sub-agents | `--parallel 2` maximum by default. MTP gains disappear at higher concurrency (§5.2). |
| Thinking output | Qwen thinks by default. Goose shows `<think>` blocks, and non-thinking mode is set per request with `chat_template_kwargs: {enable_thinking: false}`. |
| Context | The provider advertises `context_limit: 131072`, matching `-c`, so Goose compacts before overflowing. |
| Access control (optional) | `--api-key` on the backend; set the same key in the Goose provider. |

## 9. Repository layout and harness components

Everything needed for a fresh deployment is in the repo. No binaries or models are committed (see `.gitignore`).

```
ai-harness/
  README.md, DESIGN.md, .gitignore
  inference/                   # backend: build + run llama-server
    LLAMA_CPP_COMMIT           # pinned llama.cpp SHA (verified qwen4exp + draft-mtp)
    build.sh                   # shallow-fetch the pinned commit, Metal static build (cmake via uv)
    download.sh                # hf download per profile; reuses files already in the HF cache
    serve.sh                   # llama-server with profile flags (foreground; wrapped by harness/launchd)
    profiles/*.env             # default (128k+MTP), chat (32k+MTP), chat-nomtp (OOM fallback), baseline-27b
  harness/                     # Python CLI, stdlib only (runs on the Studio's Python 3.9)
    __main__.py                # CLI; --host runs commands over SSH after rsync
    config.py gguf.py system.py preflight.py backend.py watchdog.py agent.py remote.py client.py bench.py
    launchd/ai.harness.watchdog.plist   # user LaunchAgent template (KeepAlive, ProcessType Interactive)
  client/                      # OpenAI-protocol connection material
    examples/curl.sh, examples/openai_sdk.py
    goose/qwen_flash_next.json, goose/README.md
```

Runtime state lives under `AIH_HOME` (`~/ai-harness` on the Studio): `llama.cpp/`, `logs/`, `run/`, `slots/`. Models live in `~/models/`.

### CLI
| Command | Does |
|---|---|
| *(all commands)* | `--host ai@192.168.1.49` runs the command against the Studio over SSH. If omitted, the command runs locally. The default host can be set in `profiles.toml`. |
| `setup` | Installs cmake via uv, then builds llama.cpp at a pinned commit |
| `download [profile]` | Fetches the GGUFs (resumable) and verifies sizes |
| `preflight [profile]` | Runs every check in the list below and prints pass/fail with fixes |
| `serve [profile]` | Runs preflight, starts the backend detached, waits for `/health` |
| `stop` / `restart` / `status` | Manages the process; `status` shows memory, tok/s, and slot state |
| `chat` | Streaming REPL with a thinking/non-thinking toggle and the recommended samplers |
| `goose-config` | Prints, or with `--install` copies, the Goose provider file. It never overwrites existing providers. |
| `bench [profiles…]` | Measures pp512/tg128, TTFT, MTP acceptance, and peak wired memory, then writes a markdown table |

### Preflight checks
1. The `llama-server` binary exists, and `--help` lists `draft-mtp`.
2. All shards are present, and the size and GGUF magic check out.
3. **The last shard contains only `per_layer_token_embd`.** No GPU tensor may share a shard with the table.
4. The MTP head's arch matches `qwen4exp` and it contains the nextn tensors.
5. `iogpu.wired_limit_mb ≥` profile need + margin. If not, print the exact `sudo sysctl` line.
6. Free memory ≥ profile GPU total + 4 GB. Warn about large resident processes, such as `rpcsvchost` and other model servers (Ollama, LM Studio, oMLX).
7. Port is free, and free disk space is ≥ 10 GB for slot saves and logs.

### Watchdog
- Polls `memory_pressure`, `vm_stat` swapouts, and `sysctl vm.swapusage` every 5 s.
- Warns when swap use rises during decode. That means a mis-budget, because the GPU-wired set should never swap.
- On a Metal OOM in the backend log, it auto-restarts **once** with a fallback: the MTP Q4_K_M head, and if that still fails, no MTP and a smaller ctx. Then it reports what happened.
- Exposes a summary on `harness status`. Optionally it can also scrape `llama-server --metrics`.

### Defaults sent by the client
- Thinking mode: `temperature 1.0, top_p 0.95, top_k 20, min_p 0`.
- Non-thinking mode: `temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5`.
- These come from the Qwen model card.

### Example backend command (`default` profile)
```
llama-server \
  -m ~/models/qwen3.8-flash-next/IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf \
  -md ~/models/qwen3.8-flash-next/MTP/mtp-Qwen3.8-Flash-Next-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 1 -ngld 99 \
  -ngl 99 -fit off -c 131072 -fa on -ctk q8_0 -ctv q8_0 -lm mmap -lzm on \
  -t 16 -tb 16 --parallel 2 -kvu \
  --slot-save-path ~/ai-harness/slots --jinja --metrics \
  --alias qwen3.8-flash-next --host 0.0.0.0 --port 8080
```

## 10. Risks and open questions

| Risk | Mitigation |
|---|---|
| The self-contained MTP head may not load on mainline (unsloth's README says mainline lacks support, but the source shows `graph_mtp`) | First milestone tests this. Fallback is the unsloth fork, pinned. |
| ~~MTP acceptance with GSQ IQ3_XXS weights was unmeasured~~ | **Resolved:** acceptance measured at 74–96% with n-max 1 (Appendix A). |
| `rpcsvchost` holding about 15 GB at check time | Preflight warns. You may want to identify and stop it. |
| Wired limit resets after reboot | Preflight detects this and prints the command to fix it. |
| M1 Ultra speed vs AtomicChat's M5 Max numbers (36 tok/s) | **Estimate only**: about 25–40 tok/s tg on the base model, about 1.2–1.5× with MTP at temp 1.0. Bench will measure the real figures. |
| Download time (~10.7 MB/s) | Run the downloads in the background; they resume if interrupted. |
| Vision (mmproj) | Out of scope for v1. It adds about 1–2 GB, which still fits alongside IQ3_XXS + MTP at 32k. |

**Questions for you:**
1. ~~Which default?~~ Decided: ISTA IQ3_XXS + MTP at 128k context.
2. Clients: LAN-only on `0.0.0.0:8080`, or bind to localhost and put an API key or reverse proxy in front?
3. Does the harness need anything beyond a supervisor and CLI, such as a web UI, agent/tool-calling loop, or multi-user queueing?
4. ~~Where does the harness run?~~ Decided: CLI on your Mac, with the backend and watchdog on the Studio (§8.1). The user LaunchAgent needs no sudo. Persisting the wired limit still needs a root LaunchDaemon, which you would install yourself.

## 11. Milestones

1. **Backend bring-up**: build llama.cpp, then manually load IQ3_XXS and confirm the table stays unwired (check wired memory against the file size).
2. **MTP verification**: confirm `draft acceptance = …` appears in the log, and A/B tg with and without MTP at temp 0 and temp 1.
3. **Harness v1**: preflight, serve/stop/status, and chat.
4. **Bench**: MTP on vs off, plus ngram-mod, at 32k and 128k context. Fill in the real numbers in §6.
5. **Remote mode and clients**: `--host` over SSH, the LaunchAgent watchdog, and the Goose provider, verified end-to-end with a Goose tool-calling session.
6. **Hardening**: watchdog auto-fallback and slot persistence.

## Appendix A: measured results

### Qwen3.8-Flash-Next IQ3_XXS on the M1 Ultra (2026-10-31)

| Metric | Result |
|---|---|
| Load time | 28 s cold, 4 s warm (weights already in page cache) |
| Wired GPU memory | **53.6 GB** at 128k context with the MTP head. Swap never grew. About 5–7 GB stayed available. |
| n-gram table | 28.8 GB shard stays on SSD with `-lm mmap -lzm on`; it is never wired |
| Correctness | Chat returned "391"; tool call `get_weather({"city":"Paris"})` with `finish_reason=tool_calls` |
| Prefill speed | ~355 tok/s at 42k and at 123k prompt tokens. A full 123k prompt takes **5.9 min**. |

Decode speed, short prompts, mean of 3 runs (tok/s):

| Speculation | temp 0 | temp 0.7 | MTP acceptance |
|---|---|---|---|
| None | 31.5 | 31.5 | – |
| MTP, `--spec-draft-n-max 2` | 30.4 | 28.6 | 52–92% |
| **MTP, `--spec-draft-n-max 1` (chosen)** | **33.9** | **32.5** | 74–96% |

Decode at long context (MTP n-max 2):
- **31.8 tok/s at 42k** tokens
- **27.0 tok/s at 123k** tokens, with 82% acceptance

Findings:
- **MTP with n-max 2 is a net loss on the M1 Ultra.** Verifying 3 tokens through 512-expert MoE layers costs more than it saves whenever acceptance falls below ~85%. With n-max 1 it is a steady +3–8% over no MTP, so both MTP profiles now use `DRAFT_N_MAX=1`.
- **`--parallel 2` without `-kvu` splits the context**, so each slot got only 64k. `serve.sh` now passes `-kvu` (unified KV), giving every request the full 128k.
- **The memory estimate was slightly conservative.** The real footprint was 53.6 GB against the 55 GB estimate. The `default` budget is now 54 GB, and preflight's OS reserve dropped from 4 GB to 2 GB.
- **Flash-Next vs the 27B baseline on the same machine:** 32.5 vs 17.7 tok/s decode on short prompts (**1.8×**), and 27.0 tok/s at 123k vs 11.4 tok/s for the 27B at 11k. Flash-Next is better on every published benchmark as well.

**Pipeline smoke test with the `baseline-27b` profile** (Qwen3.8-27B UD-Q4_K_XL, already in the HF cache):

| Check | Result |
|---|---|
| download | Reused the 17.6 GB file from the HF cache via symlink, with no network transfer |
| preflight | All ✅ (wired limit 60416 MB, 57.8 GB available) |
| serve | Healthy after 14 s; 18.5 GB wired |
| chat (non-thinking) | Correct answer ("391") |
| tool calling | `get_weather({"city":"Paris"})` returned with `finish_reason=tool_calls` |
| bench, short prompt | **17.7 tok/s** decode |
| bench, 11k-token prompt | 176 tok/s prefill (64 s), **11.4 tok/s** decode at that depth |
| watchdog | Backend was killed with SIGKILL; the watchdog restarted it within 5 s and health returned to ok |

The 27B decode speed (17.7 tok/s) is below the earlier 20–24 estimate, so the Flash-Next estimates in §6 may also be optimistic. The real figures will come from benchmarking IQ3_XXS.

## Appendix B: current state on the Studio
- Models downloaded:
  - `~/models/qwen3.8-flash-next/` holds IQ3_XXS (75.8 GB) and the MTP Q8_0 head (4.1 GB).
  - The 27B baseline is symlinked from the HF cache.
- llama.cpp `0ec4027a2a01` is built at `~/ai-harness/llama.cpp/build/bin/`.
- **The backend is running** with the `default` profile (128k, MTP n-max 1) on `:8080`.
- The watchdog LaunchAgent is **not** installed. It has only been tested in the foreground.
