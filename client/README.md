# client/

The backend speaks the **OpenAI chat-completions protocol**, so any OpenAI-compatible tool can use it.

| Setting | Value |
|---|---|
| Base URL | `http://192.168.1.49:8080/v1` |
| Model | `qwen3.8-flash-next` (the baseline profile serves `qwen3.8-27b`) |
| API key | Not required by default. If the backend is started with `API_KEY` set in the profile, send `Authorization: Bearer <key>`. |
| Features | Streaming, tool/function calling, `reasoning_content` for thinking output, `/v1/models`, `/health`, `/metrics` |

## Recommended sampling (from the Qwen model card)

| Mode | Parameters |
|---|---|
| Thinking (default) | `temperature 1.0, top_p 0.95, top_k 20, min_p 0` |
| Non-thinking | `temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5`, plus `"chat_template_kwargs": {"enable_thinking": false}` |

## Contents

| Path | What it is |
|---|---|
| `examples/curl.sh` | Model list, chat and a tool call with curl |
| `examples/openai_sdk.py` | Streaming thinking mode and non-thinking mode with the official `openai` package |
| `goose/` | Goose custom provider and install steps |

For an interactive check without any SDK, run `python3 -m harness --host ai@192.168.1.49 chat`.

## Tips for agent tools (Goose, opencode, …)

- **Timeout:** the first turn of a session prefills the tool schemas, so allow a long first-response timeout, around 600 s. Later turns hit the prompt cache.
- **Context:** set the tool's context limit to the profile's `CTX` (131072 for `default`).
- **Concurrency:** keep it at 2 or fewer. MTP speedups vanish under heavy concurrency.
