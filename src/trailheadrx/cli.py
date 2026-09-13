"""Command line. Three verbs:

  python -m trailheadrx ingest [--rebuild]      index every downloaded document
  python -m trailheadrx ask --payer ... --lob ... --drug ... "question"
  python -m trailheadrx status                   what is indexed, dry-run or live

Run from the repo root with the virtual environment active.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trailheadrx")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="parse, chunk, embed, and index the corpus")
    s.add_argument("--rebuild", action="store_true", help="drop the index and start over")

    s = sub.add_parser("ask", help="ask a Layer 1 question")
    s.add_argument("question")
    s.add_argument("--payer", required=True, help='e.g. "UnitedHealthcare"')
    s.add_argument("--lob", required=True, choices=["commercial", "marketplace", "medicaid_mco", "medicaid_ffs", "medicare_advantage"])
    s.add_argument("--drug", required=True, help='e.g. "Emgality"')
    s.add_argument("--self-funded", choices=["yes", "no"], help="is the employer plan self-funded (if known)")
    s.add_argument("--no-judge", action="store_true", help="skip the LLM-as-judge step")
    s.add_argument("--json", action="store_true", help="print the full answer as JSON")

    sub.add_parser("status", help="index summary and mode")

    a = p.parse_args(argv)

    if a.cmd == "ingest":
        from .ingest import ingest
        print(json.dumps(ingest(rebuild=a.rebuild), indent=2))
        return 0

    if a.cmd == "status":
        from .ingest import index_summary
        print("mode:", "DRY RUN (set ANTHROPIC_API_KEY to go live)" if config.dry_run() else "live")
        print("models:", config.model("small"), "/", config.model("strong"), "/", config.model("embedding"))
        rows = index_summary()
        if not rows:
            print("index: empty — run `python -m trailheadrx ingest`")
        for r in rows:
            print(f"  {r['chunks']:4d} chunks  {r['payer']}  [{r['line_of_business']}/{r['benefit_type']}]  {r['file']}")
        return 0

    if a.cmd == "ask":
        from dataclasses import asdict
        from .pipeline import answer
        sf = None if a.self_funded is None else (a.self_funded == "yes")
        ans = answer(a.question, a.payer, a.lob, a.drug, self_funded=sf, use_judge=not a.no_judge)
        print(json.dumps(asdict(ans), indent=2) if a.json else ans.render())
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
