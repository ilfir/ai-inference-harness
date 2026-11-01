"""Small OpenAI-compatible client (stdlib only) used by `chat` and `bench`."""

import json
import urllib.request

# Recommended samplers from the Qwen3.8-Flash-Next model card.
THINKING = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0}
NON_THINKING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}


class Client:
    def __init__(self, base_url, api_key=None, timeout=1800):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, path, body=None):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        return urllib.request.urlopen(req, timeout=self.timeout)

    def models(self):
        with self._request("/v1/models") as r:
            return [m["id"] for m in json.load(r).get("data", [])]

    def complete(self, messages, thinking=True, **params):
        body = dict(THINKING if thinking else NON_THINKING, messages=messages, stream=False, **params)
        body.setdefault("chat_template_kwargs", {"enable_thinking": thinking})
        with self._request("/v1/chat/completions", body) as r:
            return json.load(r)

    def stream(self, messages, thinking=True, **params):
        """Yield ("reasoning" | "content", text) pieces, then ("done", final chunk dict)."""
        body = dict(THINKING if thinking else NON_THINKING, messages=messages, stream=True, **params)
        body.setdefault("chat_template_kwargs", {"enable_thinking": thinking})
        last = {}
        with self._request("/v1/chat/completions", body) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                last = chunk
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("reasoning_content"):
                        yield "reasoning", delta["reasoning_content"]
                    if delta.get("content"):
                        yield "content", delta["content"]
        yield "done", last
