"""Optional: polish a rule-based summary into prose with the Anthropic Messages API.

Uses only the standard library. Needs ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


def polish(summary_md: str, period: str, cfg: dict, raw_items: list[str] | None = None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    s = cfg["summary"]
    prompt = (
        f"Below is an automatically grouped log of what I worked on ({period}).\n"
        f"{s['llm_style']}\n"
        "Keep every substantive item; merge duplicates; do not invent work. "
        "Return Markdown only.\n\n"
        f"<summary>\n{summary_md}\n</summary>\n"
    )
    if raw_items:
        prompt += "\n<raw_items>\n" + "\n".join(f"- {i}" for i in raw_items) + "\n</raw_items>\n"
    body = json.dumps(
        {
            "model": s["llm_model"],
            "max_tokens": int(s["llm_max_tokens"]),
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise LLMError(f"API error {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Network error: {e.reason}") from e
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if not text.strip():
        raise LLMError("Empty response from model")
    return text.strip() + "\n"
