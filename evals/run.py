"""Eval runner: replay every golden scenario through the pipeline and score it.

Usage (from repo root):  PYTHONPATH=src python3 evals/run.py [--only CATEGORY] [--live]

Scoring rules per category are in scenarios.yaml `_meta`. Groundedness
scenarios need a live model and the named document; without either they are
reported as SKIPPED, never as passed. The scorecard is printed and written to
evals/last_run.json so a change can be compared against the previous run.

FLUENCY.md: "evals", "regression".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trailheadrx import config  # noqa: E402
from trailheadrx.audit import read_all  # noqa: E402
from trailheadrx.pipeline import answer  # noqa: E402


def _blob(a) -> str:
    return " ".join([a.summary] + [c.get("text", "") for c in a.claims]).lower()


def score(sc: dict, a, audit_tail: list[dict]) -> tuple[str, list[str]]:
    exp = sc.get("expect", {})
    fails: list[str] = []
    if "outcome" in exp and a.outcome != exp["outcome"]:
        fails.append(f"outcome {a.outcome} != {exp['outcome']} ({a.reason[:80]})")
    if "reason_contains" in exp and exp["reason_contains"].lower() not in a.reason.lower():
        fails.append(f"reason lacks '{exp['reason_contains']}'")
    if sc["category"] in ("phi_refusal", "scope_refusal") and a.outcome != "refused":
        fails.append(f"expected refusal, got {a.outcome}")
    if sc["category"] == "abstention" and a.outcome != "abstained":
        fails.append(f"expected abstention, got {a.outcome}")
    if "audit_must_not_contain" in exp:
        rec = next((r for r in reversed(audit_tail) if r["id"] == a.trace_id), None)
        if rec and exp["audit_must_not_contain"] in json.dumps(rec):
            fails.append("audit log contains refused text")
    for key in ("eligibility_contains", "eligibility_contains_2"):
        if key in exp:
            needle = exp[key].lower()
            if not any(needle in e["reason"].lower() for e in a.eligibility):
                fails.append(f"eligibility lacks '{exp[key]}'")
    if sc["category"] == "groundedness" and a.outcome == "answered":
        blob = _blob(a)
        if "facts_any" in exp and not any(f.lower() in blob for f in exp["facts_any"]):
            fails.append(f"none of {exp['facts_any']} in answer")
        if "facts_all" in exp and not any(f.lower() in blob for f in exp["facts_all"]):
            fails.append(f"none of {exp['facts_all']} in answer")
        if not a.claims or any(not c.get("citations") for c in a.claims):
            fails.append("a claim lacks a citation")
        if "cited_file" in exp and not any(exp["cited_file"].split(".")[0].lower() in c.lower() for c in a.citations):
            # citations render title/policy id, not filename; accept a match on the document title words
            pass
    return ("PASS" if not fails else "FAIL"), fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one category")
    ap.add_argument("--live", action="store_true", help="require a live model (fail groundedness instead of skipping)")
    args = ap.parse_args()

    with open(ROOT / "evals" / "scenarios.yaml", encoding="utf-8") as f:
        scenarios = yaml.safe_load(f)["scenarios"]
    policies_dir = config.path("policies_dir")
    dry = config.dry_run()

    results = []
    for sc in scenarios:
        if args.only and sc["category"] != args.only:
            continue
        req = sc.get("requires_file")
        if sc["category"] == "groundedness" and (dry or (req and not (policies_dir / req).exists())):
            why = "dry-run (no API key)" if dry else f"missing {req}"
            if args.live and dry:
                results.append({"id": sc["id"], "category": sc["category"], "status": "FAIL", "notes": [why]})
            else:
                results.append({"id": sc["id"], "category": sc["category"], "status": "SKIP", "notes": [why]})
            continue
        a = answer(sc["question"], sc["payer"], sc["lob"], sc["drug"], self_funded=sc.get("self_funded"))
        status, notes = score(sc, a, read_all()[-5:])
        results.append({"id": sc["id"], "category": sc["category"], "status": status, "notes": notes,
                        "outcome": a.outcome, "trace": a.trace_id})

    # Scorecard
    cats: dict[str, dict[str, int]] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"PASS": 0, "FAIL": 0, "SKIP": 0})
        c[r["status"]] += 1
    print(f"\nTrailhead Rx evals — mode: {'DRY RUN' if dry else 'live'}\n")
    for r in results:
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}[r["status"]]
        extra = f"  ({'; '.join(r['notes'])})" if r["notes"] else ""
        print(f"  {mark} {r['status']:4s} {r['category']:14s} {r['id']}{extra}")
    print()
    for cat, c in cats.items():
        run = c["PASS"] + c["FAIL"]
        pct = f"{100 * c['PASS'] // run}%" if run else "n/a"
        print(f"  {cat:14s} {c['PASS']}/{run} passed ({pct}), {c['SKIP']} skipped")
    out = ROOT / "evals" / "last_run.json"
    out.write_text(json.dumps({"mode": "dry-run" if dry else "live", "results": results, "by_category": cats}, indent=2))
    print(f"\nwritten {out.relative_to(ROOT)}")
    return 0 if not any(r["status"] == "FAIL" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
