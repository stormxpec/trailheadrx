"""Class comparison: how the plan treats each medicine in the same class.

This is the first fan-out in the app (FLUENCY.md: "fan-out", "structured
output"). For the chosen drug's class — CGRP preventives, or CGRP acute
agents — it runs, per drug and in parallel:

  plan match → hybrid retrieval → one small-model extraction call that fills
  a fixed record: is approval required, what must be tried first, how long
  the tries last, an estimated months-to-approval, and passage citations.

Then it verifies every record's citations exist (code) and renders a table.
It compares COVERAGE PATHS only. It never compares how well the medicines
work — that is rule G1 and it is restated in the extraction prompt.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import llm
from .retrieve import build_packet, load_drugs
from .rules import rules_text

CLASS_GROUPS = {
    # preventives that a plan typically treats as one step-therapy class
    "cgrp_preventive": ["Aimovig", "Ajovy", "Emgality", "Vyepti", "Qulipta", "Nurtec ODT"],
    "cgrp_acute": ["Nurtec ODT", "Ubrelvy", "Zavzpret"],
}


def class_for(drug_brand: str) -> tuple[str, list[str]]:
    for name, members in CLASS_GROUPS.items():
        if any(m.split()[0].lower() == drug_brand.split()[0].lower() for m in members):
            return name, members
    return "", []


EXTRACT_SYSTEM = (
    "You read passages from a health plan's published policy and fill in a short record about ONE medicine. "
    "Use only the passages, and only the parts that apply to THIS medicine. The passages often describe several "
    "medicines side by side; a requirement stated for a different medicine is NOT this medicine's requirement. A "
    "medicine can never require trying itself first — if you find yourself writing that, you have mixed up drugs. "
    "Never compare how well medicines work; only what the plan requires. "
    "Reply with a single JSON object and nothing else:\n"
    '{"approval_required": true|false|null, '
    '"must_try_first": "plain-language summary of what must be tried before this medicine, or \'nothing\' or \'not stated\'", '
    '"trial_length": "e.g. two months each, or \'not stated\'", '
    '"months_estimate_low": int|null, "months_estimate_high": int|null, '
    '"notes": "one short plain sentence on anything unusual (state carve-outs, age rules, other CGRP drugs required first)", '
    '"citations": [passage numbers used]}\n'
    "For months_estimate: if starting from zero, add the required trial lengths as if done one after another, plus "
    "about half a month for the plan's review. If the medicine requires trying other CGRP medicines first, include "
    "their trial lengths too. Null if the passages give no lengths."
)


@dataclass
class ClassRow:
    drug: str
    status: str                       # ok | not_in_corpus | no_passages | extract_failed
    approval_required: bool | None = None
    must_try_first: str = ""
    trial_length: str = ""
    months_low: int | None = None
    months_high: int | None = None
    notes: str = ""
    citations: list[str] = field(default_factory=list)
    reason: str = ""


def _one(drug: str, payer: str, lob: str) -> tuple[ClassRow, llm.LLMResult | None]:
    packet = build_packet(f"What must be tried before {drug} is approved, and for how long?", payer, lob, drug, rules_text())
    pm = packet.plan_match
    if pm.confidence < 0.6:
        return ClassRow(drug, "not_in_corpus", reason=pm.reason), None
    if not packet.chunks:
        return ClassRow(drug, "no_passages", reason="No passage in the plan's documents mentions this medicine."), None
    user = f"Medicine: {drug}. Plan: {payer} ({lob}).\n\n{packet.as_prompt_text()}"
    stub = json.dumps({"approval_required": None, "must_try_first": "[dry-run]", "trial_length": "not stated",
                       "months_estimate_low": None, "months_estimate_high": None, "notes": "", "citations": [1]})
    res = llm.complete("small", EXTRACT_SYSTEM, user, max_tokens=600, dry_run_stub=stub)
    data = llm.extract_json(res.text)
    if not data:
        return ClassRow(drug, "extract_failed", reason="The extraction did not return valid JSON."), res
    n = len(packet.chunks)
    cites = [c for c in data.get("citations", []) if isinstance(c, int) and 1 <= c <= n]
    if not cites:
        return ClassRow(drug, "extract_failed", reason="No valid passage citation."), res
    row = ClassRow(
        drug, "ok", data.get("approval_required"), data.get("must_try_first", ""), data.get("trial_length", ""),
        data.get("months_estimate_low"), data.get("months_estimate_high"), data.get("notes", ""),
        [packet.chunks[c - 1].citation() for c in cites],
    )
    row._passages = {packet.chunks[c - 1].citation(): packet.chunks[c - 1].text for c in cites}  # for the judge
    return row, res


ROW_JUDGE_SYSTEM = (
    "You are a strict reviewer. You are given ONE medicine, a short record of what a health plan requires before "
    "covering it, and the passage(s) the record cites. Answer two questions using only the passage text: "
    "(1) Does the passage state these requirements FOR THIS MEDICINE specifically (not for a neighboring medicine "
    "in the same passage)? (2) Is the 'must_try_first' text accurate to the passage? "
    "Reply with JSON only: {\"verdict\": \"SUPPORTED\" | \"WRONG_DRUG\" | \"NOT_SUPPORTED\", \"reason\": \"...\"}. "
    "A record that says the medicine must be tried before itself is always WRONG_DRUG."
)


def _judge_row(row: ClassRow, packet_text_by_citation: dict[str, str]) -> tuple[str, str, llm.LLMResult | None]:
    if row.status != "ok":
        return "SKIPPED", "", None
    passages = "\n---\n".join(packet_text_by_citation.get(c, "") for c in row.citations)
    user = (f"MEDICINE: {row.drug}\nRECORD: approval_required={row.approval_required}; must_try_first={row.must_try_first}; "
            f"trial_length={row.trial_length}; notes={row.notes}\nCITED PASSAGES:\n{passages}")
    stub = json.dumps({"verdict": "SUPPORTED", "reason": "dry-run"})
    res = llm.complete("small", ROW_JUDGE_SYSTEM, user, max_tokens=600, dry_run_stub=stub)
    data = llm.extract_json(res.text) or {}
    return data.get("verdict", "NOT_SUPPORTED"), data.get("reason", ""), res


def compare_class(drug: str, payer: str, lob: str) -> tuple[str, list[ClassRow], list[llm.LLMResult]]:
    name, members = class_for(drug)
    if not members:
        return "", [], []
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda d: _one(d, payer, lob), members))
    rows = [r for r, _ in results]
    calls = [res for _, res in results if res]
    # Verify every row: a wrong-drug attribution is the classic fan-out failure
    # (shared documents → neighboring drug's rule lands on the wrong row).
    with ThreadPoolExecutor(max_workers=6) as ex:
        judged = list(ex.map(lambda r: _judge_row(r, getattr(r, "_passages", {})), rows))
    for r, (verdict, reason, res) in zip(rows, judged):
        if res:
            calls.append(res)
        if verdict in ("WRONG_DRUG", "NOT_SUPPORTED"):
            r.status = "unverified"
            r.reason = ("We extracted a rule for this medicine but a second check found it was actually about a "
                        "different medicine in the same document, so we are not showing it." if verdict == "WRONG_DRUG"
                        else "We extracted a rule for this medicine but could not confirm it against the document, so we are not showing it.")
    return name, rows, calls


def render_table(chosen: str, name: str, rows: list[ClassRow]) -> str:
    label = {"cgrp_preventive": "CGRP migraine-prevention medicines", "cgrp_acute": "CGRP medicines for treating an attack"}.get(name, "this class")
    lines = ["HOW YOUR PLAN TREATS THE OTHER " + label.upper(),
             "  This compares the paperwork and waiting, not the medicines themselves. Which one is right for you is "
             "your doctor's call; how hard your plan makes each one is worth knowing before that conversation.", ""]
    for r in rows:
        mark = "→ " if r.drug.split()[0].lower() == chosen.split()[0].lower() else "  "
        if r.status != "ok":
            lines.append(f"{mark}{r.drug}: {r.reason}")
            continue
        wait = "no estimate"
        if r.months_high:
            wait = f"about {r.months_high} months" if not r.months_low or r.months_low == r.months_high \
                else f"about {r.months_low}–{r.months_high} months"
        appr = {True: "approval needed", False: "no approval needed", None: "approval: not stated"}[r.approval_required]
        lines.append(f"{mark}{r.drug} — {appr}; wait if starting from zero: {wait}")
        lines.append(f"     Try first: {r.must_try_first} ({r.trial_length})")
        if r.notes:
            lines.append(f"     Note: {r.notes}")
        lines.append(f"     Source: {r.citations[0]}")
    return "\n".join(lines)
