"""Throughput benchmark over the OpenAI API, using llama-server's per-request timings."""

import random
import time

from .client import Client

PROMPTS = [
    "Write a Python function that merges overlapping intervals, with a short docstring.",
    "Explain how a B-tree differs from a binary search tree in about 150 words.",
    "List ten practical tips for reducing memory usage in a long-running Node.js service.",
]
FILLER = ("The quick brown fox jumps over the lazy dog while the archivist catalogues "
          "another dusty ledger of tide tables, shipping manifests and weather notes. ")


def _filler(tokens):
    # Roughly 20 tokens per FILLER sentence.
    return FILLER * max(1, tokens // 20)


def run(base_url, model=None, max_tokens=256, fill=0, temperature=None, runs=None, api_key=None, log=print):
    client = Client(base_url, api_key)
    model = model or client.models()[0]
    rows = []
    for i, prompt in enumerate(PROMPTS[: runs or len(PROMPTS)]):
        # A random nonce up front defeats the prompt cache so prefill is measured honestly.
        content = "[run %08x]\n" % random.getrandbits(32)
        if fill:
            content += "Context document:\n" + _filler(fill) + "\n\nIgnore the document above. "
        content += prompt
        params = {"model": model, "max_tokens": max_tokens}
        if temperature is not None:
            params["temperature"] = temperature
        t0 = time.time()
        resp = client.complete([{"role": "user", "content": content}], thinking=False, **params)
        wall = time.time() - t0
        t = resp.get("timings", {})
        drafted, accepted = t.get("draft_n") or 0, t.get("draft_n_accepted") or 0
        row = {
            "run": i + 1,
            "prompt_tokens": t.get("prompt_n") or resp.get("usage", {}).get("prompt_tokens"),
            "prefill_tps": t.get("prompt_per_second"),
            "ttft_s": (t.get("prompt_ms") or 0) / 1000,
            "gen_tokens": t.get("predicted_n") or resp.get("usage", {}).get("completion_tokens"),
            "decode_tps": t.get("predicted_per_second"),
            "accept": (accepted / drafted) if drafted else None,
            "wall_s": wall,
        }
        rows.append(row)
        log("  run %d: %s" % (i + 1, _fmt_row(row)))
    return model, rows


def _num(v, fmt):
    return fmt % v if isinstance(v, (int, float)) else "-"


def _fmt_row(r):
    return "prefill %s tok/s, decode %s tok/s, MTP accept %s" % (
        _num(r["prefill_tps"], "%.0f"), _num(r["decode_tps"], "%.1f"),
        _num(r["accept"] * 100 if r["accept"] is not None else None, "%.0f%%"))


def markdown(model, rows, fill):
    out = ["Model `%s`, context fill ~%d tokens" % (model, fill), "",
           "| run | prompt tok | prefill tok/s | TTFT s | gen tok | decode tok/s | MTP accept |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| %d | %s | %s | %s | %s | %s | %s |" % (
            r["run"], _num(r["prompt_tokens"], "%d"), _num(r["prefill_tps"], "%.0f"), _num(r["ttft_s"], "%.1f"),
            _num(r["gen_tokens"], "%d"), _num(r["decode_tps"], "%.1f"),
            _num(r["accept"] * 100 if r["accept"] is not None else None, "%.0f%%")))
    dec = [r["decode_tps"] for r in rows if r["decode_tps"]]
    if dec:
        out += ["", "Mean decode: **%.1f tok/s**" % (sum(dec) / len(dec))]
    return "\n".join(out)
