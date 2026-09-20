"""Nightly precompute and QA sweep.

Two jobs in one pass, because they need the same work done:

  1. Warm the cache. The menu is finite — held plan × medicine × preset
     question, no free text — so every combination can be answered ahead of
     time and served instantly. This runs the same `compute()` the website
     runs, so what a visitor gets is exactly what the sweep produced.

  2. Catch drift. Each fresh answer is compared with the last one for the
     same request (kept in the sweep's own record on disk): the summary,
     the numbered steps, and the lead-time months. A difference when the
     policy document did not change is the model reading the same text
     differently — worth a human look. A difference right after the refresh
     job reported a changed document is expected, and the report says which.

Budget: the sweep stops when today's estimated spend (audit trace) would
pass `precompute.daily_budget_usd` in config.yaml, and resumes where it left
off the next night. `--pairs` limits the run to the most-asked combinations
(from the audit trace) for a cheap partial sweep.

No new model logic lives here; it is a loop around the pipeline plus a diff.

FLUENCY.md: "precomputation", "cache warming", "regression sweep".
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .audit import read_all


@dataclass
class Drift:
    key: str
    what: str
    before: str
    after: str


@dataclass
class SweepReport:
    started: str
    computed: int = 0
    skipped_cached: int = 0
    abstained: int = 0
    abstain_reasons: Counter = field(default_factory=Counter)
    failed: list[str] = field(default_factory=list)
    drift: list[Drift] = field(default_factory=list)
    stopped_for_budget: bool = False
    spent_usd: float = 0.0

    def text(self) -> str:
        lines = [f"Trailhead Rx nightly sweep — {self.started}",
                 f"{self.computed} answered · {self.skipped_cached} already cached · {self.abstained} abstained · "
                 f"{len(self.failed)} failed · est. ${self.spent_usd:.2f}"
                 + (" · STOPPED AT BUDGET" if self.stopped_for_budget else ""), ""]
        if self.abstain_reasons:
            lines.append("Abstained, by reason:")
            for reason, n in self.abstain_reasons.most_common(6):
                lines.append(f"  {n} × {reason[:140]}")
            lines.append("")
        if self.drift:
            lines.append(f"{len(self.drift)} answers changed since the last sweep:")
            for d in self.drift:
                lines.append(f"  {d.key}\n    {d.what}\n    was: {d.before[:160]}\n    now: {d.after[:160]}")
        else:
            lines.append("No drift: every re-answered request matched its previous summary, steps, and lead time.")
        for f in self.failed:
            lines.append(f"FAILED {f}")
        return "\n".join(lines)


def _record_path() -> Path:
    override = os.environ.get("TRAILHEADRX_SWEEP_RECORD")
    return Path(override) if override else config.ROOT / "governance" / "sweep_record.json"


def _load_record() -> dict:
    p = _record_path()
    return json.load(open(p, encoding="utf-8")) if p.exists() else {}


def _save_record(rec: dict) -> None:
    p = _record_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    json.dump(rec, open(p, "w", encoding="utf-8"), indent=2, sort_keys=True)


def menu(presets: list[tuple[str, str]] | None = None) -> list[dict]:
    """Every request the form can produce without free text."""
    from .web.app import PRESETS, _drugs
    from .web.plans import choices
    presets = presets or [(k, q) for k, q in PRESETS if k != "other"]
    out = []
    for c in choices():
        if c.self_funded:          # alias rows point at the same documents as their parent
            continue
        for d in _drugs():
            for key, question in presets:
                out.append({"payer": c.payer, "lob": c.lob, "drug": d["brand"], "question": question,
                            "preset": key, "self_funded": None, "compare": False, "plans": False, "free_text": False})
    return out


def most_asked(limit: int) -> list[tuple[str, str]]:
    """(payer, drug) pairs by how often the audit trace shows them ANSWERED.
    Abstained requests are left out — re-running "we don't hold that plan"
    twenty times a night would warm nothing — and the list is topped up
    from the menu so the sweep always has real work."""
    counts: Counter = Counter()
    for rec in read_all():
        req = rec.get("request") or {}
        if req.get("payer") and req.get("drug") and rec.get("outcome", "answered") == "answered":
            counts[(req["payer"], req["drug"])] += 1
    top = [k for k, _ in counts.most_common(limit)]
    if len(top) < limit:
        seen = set(top)
        for r in menu():
            k = (r["payer"], r["drug"])
            if k not in seen:
                top.append(k)
                seen.add(k)
            if len(top) >= limit:
                break
    return top


def _signature(ans, table) -> dict:
    months = ""
    if ans.wait_estimate and ans.wait_estimate.get("months_high"):
        months = f"{ans.wait_estimate.get('months_low')}-{ans.wait_estimate.get('months_high')}"
    return {"outcome": ans.outcome, "summary": ans.summary, "points": list(ans.summary_points), "months": months}


def sweep(pairs: int | None = None, budget_usd: float | None = None, dry: bool = False) -> SweepReport:
    from .web.gate import spend_today
    from .web.jobs import _cache_get, compute

    report = SweepReport(started=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    budget = budget_usd if budget_usd is not None else float(config.CONFIG.get("precompute", {}).get("daily_budget_usd", 3.0))
    requests = menu()
    if pairs:
        top = set(most_asked(pairs))
        requests = [r for r in requests if (r["payer"], r["drug"]) in top]
    record = _load_record()
    start_spend = spend_today().today_usd

    for r in requests:
        key = f"{r['payer']} · {r['lob']} · {r['drug']} · {r['preset']}"
        if _cache_get(r):
            report.skipped_cached += 1
            continue
        spent = spend_today().today_usd - start_spend
        if spent >= budget:
            report.stopped_for_budget = True
            break
        if dry:
            report.computed += 1
            continue
        try:
            ans, table, _plans, _trace = compute(r, use_cache=False)
        except Exception as e:
            report.failed.append(f"{key}: {type(e).__name__}: {e}")
            continue
        if ans.outcome != "answered":
            report.abstained += 1
            report.abstain_reasons[(ans.reason or ans.outcome).split(". ")[0]] += 1
            continue
        report.computed += 1
        sig = _signature(ans, table)
        prev = record.get(key)
        if prev:
            for what in ("summary", "points", "months"):
                if prev.get(what) != sig[what]:
                    report.drift.append(Drift(key, what, json.dumps(prev.get(what)), json.dumps(sig[what])))
        record[key] = dict(sig, on=datetime.now(timezone.utc).date().isoformat())
    report.spent_usd = max(0.0, spend_today().today_usd - start_spend)
    if not dry:
        _save_record(record)
    return report


def run_and_notify(pairs: int | None = None, budget_usd: float | None = None) -> SweepReport:
    from .refresh import send_email
    report = sweep(pairs=pairs, budget_usd=budget_usd)
    print(report.text())
    if report.drift or report.failed or report.stopped_for_budget:
        if not send_email(f"Trailhead Rx sweep: {len(report.drift)} drifted, {len(report.failed)} failed", report.text()):
            print("\n(email not configured: set RESEND_API_KEY and REFRESH_EMAIL_TO)")
    return report
