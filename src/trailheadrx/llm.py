"""The one place the app talks to Claude.

Two tiers (small, strong) named in config.yaml. Every call returns the text
plus token counts so the audit log can record cost. When no API key is set,
the client is in dry-run mode: it returns a deterministic stand-in answer
built from the packet, so ingestion, retrieval, guardrails, verification,
and logging can all be exercised without spending anything. Dry-run answers
are clearly marked and never shown to a patient.

FLUENCY.md: "model tiering", "structured output", "sampling".
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from . import config


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    dry_run: bool
    stop_reason: str = "end_turn"    # "max_tokens" means the reply was cut off


PRICES_PER_MTOK = {  # from the models page, Sep 2026; used for the audit cost estimate only
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    key = next((k for k in PRICES_PER_MTOK if model.startswith(k)), None)
    if not key:
        return 0.0
    pi, po = PRICES_PER_MTOK[key]
    return round((input_tokens * pi + output_tokens * po) / 1_000_000, 6)


_client = None


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.api_key())
    return _client


def complete(tier: str, system: str, user: str, max_tokens: int | None = None, dry_run_stub: str | None = None) -> LLMResult:
    """One model call. `tier` is 'small' or 'strong'."""
    model_id = config.model(tier)
    max_tokens = max_tokens or config.CONFIG["models"]["max_output_tokens"]
    t0 = time.time()

    if config.dry_run():
        text = dry_run_stub if dry_run_stub is not None else "[dry-run: no API key set]"
        return LLMResult(text, f"{model_id} (dry-run)", len(system.split()) + len(user.split()),
                         len(text.split()), int((time.time() - t0) * 1000), True)

    resp = _anthropic().messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    return LLMResult(text, model_id, resp.usage.input_tokens, resp.usage.output_tokens,
                     int((time.time() - t0) * 1000), False, getattr(resp, "stop_reason", "end_turn") or "end_turn")


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply. Models occasionally
    wrap JSON in a code fence or add a sentence; this tolerates both."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
