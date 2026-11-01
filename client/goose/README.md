# Goose provider for Qwen3.8-Flash-Next

`qwen_flash_next.json` is a Goose custom provider that points at the harness backend, `llama-server` on the Studio:
- Address: `http://192.168.1.49:8080/v1`
- Model alias: `qwen3.8-flash-next`

## Install

Install only after the backend is running (`python3 -m harness --host ai@192.168.1.49 serve`). Then copy the file and check that it lists the model:

```bash
cp client/goose/qwen_flash_next.json ~/.config/goose/custom_providers/
```

```bash
curl -s http://192.168.1.49:8080/v1/models
```

Restart Goose.app, then choose **Qwen3.8-Flash-Next (Studio)** as the provider.

You can also run `python3 -m harness goose-config --install`. It does the same copy and refuses to overwrite an existing file.

## Notes

- **Port:** this provider uses port 8080. Your existing `custom_unsloth*` providers use 8888 and stay untouched. Don't load a model in Unsloth Studio while this backend runs, because the Studio doesn't have memory for both.
- **Timeout:** `timeout_seconds` is 600, because the first turn of a new session prefills Goose's tool schemas. Later turns reuse the prompt cache.
- **`context_limit`:** 131072 matches the backend's `-c`. Change both together.
- **API key:** if you start the backend with `--api-key`, set `"requires_auth": true` and `"api_key_env"` here.
- **Thinking:** `reasoning: true` and `preserves_thinking: true` let Goose show the model's `<think>` output.
