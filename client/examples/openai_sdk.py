"""Use the backend from the official OpenAI Python SDK.

    pip install openai
    python client/examples/openai_sdk.py [base_url]
"""

import os
import sys

from openai import OpenAI

base = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.1.49:8080"
client = OpenAI(base_url=base + "/v1", api_key=os.environ.get("AIH_API_KEY", "not-needed"))
model = os.environ.get("MODEL", "qwen3.8-flash-next")

# Thinking mode (default for Qwen3.8): the reasoning arrives separately as `reasoning_content`.
stream = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "In two sentences, why do MoE models run well from SSD-backed memory?"}],
    temperature=1.0,
    top_p=0.95,
    extra_body={"top_k": 20, "min_p": 0.0},
    stream=True,
)
for chunk in stream:
    if not chunk.choices:
        continue
    delta = chunk.choices[0].delta
    text = getattr(delta, "reasoning_content", None) or delta.content
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()
print()

# Non-thinking mode, e.g. for quick tool-using agents.
resp = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Reply with just the number: 17*23"}],
    temperature=0.7,
    top_p=0.8,
    presence_penalty=1.5,
    extra_body={"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}},
)
print(resp.choices[0].message.content)
