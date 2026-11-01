#!/usr/bin/env bash
# OpenAI-protocol requests against the backend with plain curl.
# Usage: client/examples/curl.sh [base_url]   (default http://192.168.1.49:8080)
set -euo pipefail
BASE="${1:-http://192.168.1.49:8080}"
MODEL="${MODEL:-qwen3.8-flash-next}"
AUTH=()
[ -n "${AIH_API_KEY:-}" ] && AUTH=(-H "Authorization: Bearer $AIH_API_KEY")

echo "== models"
curl -s "${AUTH[@]}" "$BASE/v1/models"; echo

echo "== chat (non-thinking)"
curl -s "${AUTH[@]}" "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model": "'"$MODEL"'",
  "messages": [{"role": "user", "content": "Say hello in five words."}],
  "temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5,
  "chat_template_kwargs": {"enable_thinking": false}
}'; echo

echo "== tool call"
curl -s "${AUTH[@]}" "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d '{
  "model": "'"$MODEL"'",
  "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
  "tools": [{"type": "function", "function": {"name": "get_weather", "description": "Get weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}],
  "chat_template_kwargs": {"enable_thinking": false}
}'; echo
