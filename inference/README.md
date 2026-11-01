# inference/

This folder holds the backend: `llama-server` from llama.cpp, built with Metal at a pinned commit. Every script works on its own, and the harness wraps them.

| File | Purpose |
|---|---|
| `LLAMA_CPP_COMMIT` | The llama.cpp commit that was verified to have `qwen4exp` and its MTP graph (`--spec-type draft-mtp`) |
| `build.sh [--commit SHA] [--clean]` | Shallow-fetches that commit into `$AIH_HOME/llama.cpp` and builds `llama-server`, `llama-cli`, `llama-bench` and `llama-gguf-split` |
| `download.sh <profile>` | Downloads a profile's model and MTP head into `MODELS_DIR`, reusing files already in the HF cache |
| `serve.sh <profile> [args…]` | Runs `llama-server` in the foreground with the profile's settings |
| `profiles/*.env` | Serving profiles in shell `KEY=VALUE` format, read by these scripts and by the harness |

## Profiles

| Profile | Model | Context | MTP | GPU budget |
|---|---|---|---|---|
| `default` | Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS | 128k | Q8_0 head | ~55 GB |
| `chat` | same | 32k | Q8_0 head | ~53 GB |
| `chat-nomtp` | same | 32k | — (watchdog fallback after OOM) | ~50 GB |
| `baseline-27b` | Qwen3.8-27B UD-Q4_K_XL | 32k | — | ~22 GB |

## Flags that matter for running a larger-than-RAM model

| Flag | Why |
|---|---|
| `-lm mmap -lzm on` | Weights are mmap'd. The n-gram table (its own GGUF shard) is read row by row from SSD instead of staying resident. Never use `--no-mmap` or `--mlock`. |
| `-ngl 99 -fit off` | Every layer goes on Metal. Auto-fit mis-sizes `qwen4exp`. |
| `-md <mtp.gguf> --spec-type draft-mtp --spec-draft-n-max 1 -ngld 99` | MTP speculative decoding. It needs a self-contained head, because mainline can't borrow tensors. n-max 1 measured best on the M1 Ultra. |
| `-fa on -ctk q8_0 -ctv q8_0` | Flash attention, plus a KV cache at half the size. |
| `--cache-ram $CACHE_RAM_MB` (2048) | Caps llama-server's host-RAM prompt cache. The 8 GiB default stacked on 53.6 GB of wired weights pushed the Studio into compression and swap during long Goose sessions. |
| `--slot-save-path` (prefix caching is on by default) | Agent clients that resend the same long tool schemas only prefill the new tokens. `--cache-reuse` is not used: llama.cpp disables it for models with recurrent (DeltaNet) state. |

**Why the shard layout matters:** Metal wires every shard that contains a GPU tensor. If the n-gram table shared a shard with the weights, it would be wired too, and the first decode would fail with a Metal OOM. `harness preflight` checks this.

## Updating llama.cpp

Put the new SHA in `LLAMA_CPP_COMMIT`, run `build.sh --clean`, then `harness preflight` (which checks for `draft-mtp`), then `harness bench`.

If GitHub is slow from the host, copy a checkout across instead: `rsync` a clone of that commit into `$AIH_HOME/llama.cpp`, and `build.sh` will skip the fetch.
