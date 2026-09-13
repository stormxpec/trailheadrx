"""Tracing: the audit log. One JSON line per request, written by the pipeline
at each stage. Two readers: a human, and the eval scorer.

What is recorded: the question, plan and drug chosen, guardrail outcomes,
plan match and confidence, which chunks were retrieved (ids and citations,
not full text), the draft, verifier notes, revisions, the final outcome,
model IDs, token counts, latency, and estimated cost.

What is never recorded: anything the input guardrail refused (only the
category of refusal), card images, or any field that describes a person.

FLUENCY.md: "tracing / observability".
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config


class Trace:
    def __init__(self, request: dict):
        self.id = uuid.uuid4().hex[:12]
        self.started = datetime.now(timezone.utc)
        self.record: dict = {
            "id": self.id,
            "started_at": self.started.isoformat(),
            "request": request,
            "stages": [],
            "calls": [],
            "outcome": None,
        }

    def stage(self, name: str, **data) -> None:
        self.record["stages"].append({"stage": name, **data})

    def call(self, purpose: str, res) -> None:
        from .llm import estimate_cost_usd
        self.record["calls"].append({
            "purpose": purpose, "model": res.model, "input_tokens": res.input_tokens,
            "output_tokens": res.output_tokens, "latency_ms": res.latency_ms,
            "cost_usd": estimate_cost_usd(res.model, res.input_tokens, res.output_tokens),
            "dry_run": res.dry_run, "stop_reason": getattr(res, "stop_reason", "end_turn"),
        })

    def finish(self, outcome: str, **data) -> dict:
        self.record["outcome"] = outcome
        self.record.update(data)
        self.record["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.record["total_cost_usd"] = round(sum(c["cost_usd"] for c in self.record["calls"]), 6)
        self.record["total_latency_ms"] = int((datetime.now(timezone.utc) - self.started).total_seconds() * 1000)
        self._write()
        return self.record

    def _write(self) -> None:
        d: Path = config.path("audit_dir")
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{self.started:%Y-%m-%d}.jsonl"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.record, ensure_ascii=False) + "\n")


def read_all() -> list[dict]:
    d: Path = config.path("audit_dir")
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            out.extend(json.loads(line) for line in fh if line.strip())
    return out
