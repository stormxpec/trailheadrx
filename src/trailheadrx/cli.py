"""Command line. Six verbs:

  python -m trailheadrx ingest [--rebuild]      index every downloaded document
  python -m trailheadrx ask --payer ... --lob ... --drug ... "question"
  python -m trailheadrx status                   what is indexed, dry-run or live
  python -m trailheadrx trace [id]               why the verifier kept or dropped each claim
  python -m trailheadrx refresh [--only ...]     re-fetch every source, re-index changes, email
  python -m trailheadrx sweep [--pairs N]        precompute the menu, report drift (costs money)

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
    s.add_argument("--compare-class", action="store_true", help="also show how the plan treats the other medicines in the same class")
    s.add_argument("--json", action="store_true", help="print the full answer as JSON")

    sub.add_parser("status", help="index summary and mode")

    s = sub.add_parser("refresh", help="re-fetch every source, re-index what changed, email if configured")
    s.add_argument("--only", choices=["policies", "programs"], help="limit to one kind of source")
    s.add_argument("--no-email", action="store_true", help="print the report only")
    s = sub.add_parser("sweep", help="precompute the menu of answers and report drift (costs money)")
    s.add_argument("--pairs", type=int, help="only the N most-asked plan × medicine pairs")
    s.add_argument("--budget", type=float, help="stop after this much estimated spend (USD)")
    s.add_argument("--dry", action="store_true", help="count what would run; no model calls")
    s.add_argument("--no-email", action="store_true")
    s = sub.add_parser("trace", help="show why the verifier kept or dropped each claim (latest answer, or a trace id)")
    s.add_argument("trace_id", nargs="?", help="12-character reference from the answer page; default: the latest")

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

    if a.cmd == "refresh":
        from .refresh import refresh, run_and_notify
        if a.no_email:
            print(refresh(only=a.only).text())
        else:
            run_and_notify(only=a.only)
        return 0
    if a.cmd == "sweep":
        from .precompute import run_and_notify, sweep
        if a.dry or a.no_email:
            print(sweep(pairs=a.pairs, budget_usd=a.budget, dry=a.dry).text())
        else:
            run_and_notify(pairs=a.pairs, budget_usd=a.budget)
        return 0
    if a.cmd == "trace":
        from .audit import read_all
        recs = read_all()
        rec = next((r for r in reversed(recs) if r.get("id") == a.trace_id), None) if a.trace_id else (recs[-1] if recs else None)
        if not rec:
            print("no trace found"); return 1
        print(f"trace {rec['id']}  outcome={rec.get('outcome')}  cost=${rec.get('total_cost_usd', 0):.4f}")
        for st in rec.get("stages", []):
            name = st.get("stage", "")
            if name == "plan_match":
                print(f"\nplan match: confidence {st.get('confidence')} — {st.get('reason')}")
            elif name == "retrieval":
                for c in st.get("packet", []) or []:
                    print(f"  [{c['n']}] kw={c.get('keyword_rank') or '-'} sem={c.get('semantic_rank') or '-'} {c['citation'][:90]} | {c.get('section', '')[:40]}")
            elif name.startswith("verify"):
                print(f"\n{name}: {'passed' if st.get('passed') else 'DID NOT PASS'}")
                for n in st.get("notes", []):
                    print(f"  note: {n}")
                for pc in st.get("per_claim", []):
                    flag = "DROPPED" if pc.get("failed") else "kept   "
                    print(f"  {flag} claim {pc['claim']} cites={pc.get('citations')} overlap={pc.get('overlap')} judge={pc.get('judge', '-')}")
                    print(f"          {pc.get('text', '')[:140]}")
                    if pc.get("judge") not in (None, "SUPPORTED", "-") and pc.get("judge_reason"):
                        print(f"          judge: {pc['judge_reason'][:200]}")
            elif name.startswith("prune"):
                print(f"  pruned {st.get('dropped')}, kept {st.get('kept')}")
        return 0

    if a.cmd == "ask":
        from dataclasses import asdict
        from .pipeline import answer
        sf = None if a.self_funded is None else (a.self_funded == "yes")
        ans = answer(a.question, a.payer, a.lob, a.drug, self_funded=sf, use_judge=not a.no_judge)
        print(json.dumps(asdict(ans), indent=2) if a.json else ans.render())
        if a.compare_class:
            from .compare import compare_class, render_table
            name, rows, calls = compare_class(a.drug, a.payer, a.lob)
            if rows:
                print("\n" + render_table(a.drug, name, rows))
                cost = sum(__import__("trailheadrx.llm", fromlist=["estimate_cost_usd"]).estimate_cost_usd(c.model, c.input_tokens, c.output_tokens) for c in calls)
                print(f"\n  ({len(calls)} extraction calls, est. ${cost:.3f})")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
