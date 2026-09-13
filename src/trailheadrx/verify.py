"""The verifier: code checks first, then LLM-as-judge.

Code checks (cheap, deterministic, run every time):
  - shape: the draft parsed as JSON and has the required fields;
  - citation validity: every claim cites at least one passage that exists;
  - lexical support: for each claim, at least one cited passage shares
    enough distinctive words (numbers, drug names, key terms) with the claim
    to make the citation plausible. This catches the classic failure of a
    correct-sounding sentence pinned to an unrelated passage.

LLM-as-judge (the small model, run when a key is present):
  - for each claim, does the cited passage actually support it? The judge
    answers per claim with SUPPORTED / NOT_SUPPORTED / PARTIAL and a short
    reason. Anything not SUPPORTED becomes a revision note.

The verifier never rewrites the answer. It returns a verdict and notes; the
prompt builder decides whether to revise. FLUENCY.md: "verifier / LLM-as-judge",
"groundedness".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import config, llm
from .retrieve import ContextPacket

STOP = set("""a an the and or of to in on for with by at from as is are was were be been being this that these those it its
which who whom what when where how not no yes may might can could should would will shall than then there their they them
you your we our us he she his her if so but also any all each per one two three four five six seven eight nine ten
plan policy member patient medication drug drugs coverage covered require required requires requirement requirements must""".split())


LEXICAL_HARD_FLOOR = 0.08   # below this, a citation is almost certainly wrong


def _terms(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|\d+", text.lower())
    return {w for w in words if w not in STOP}


@dataclass
class Verdict:
    passed: bool
    notes: list[str] = field(default_factory=list)
    per_claim: list[dict] = field(default_factory=list)
    judge: llm.LLMResult | None = None


def code_checks(answer: dict | None, packet: ContextPacket) -> Verdict:
    v = Verdict(True)
    if not answer or not isinstance(answer, dict):
        return Verdict(False, ["The draft was not valid JSON matching the schema."])
    for key in ("summary", "claims", "not_in_documents"):
        if key not in answer:
            v.notes.append(f"Missing required field '{key}'.")
    claims = answer.get("claims") or []
    if not claims:
        v.notes.append("The draft contains no claims; if the passages answer nothing, say so in not_in_documents.")
    n = len(packet.chunks)
    for i, claim in enumerate(claims, start=1):
        cites = [c for c in claim.get("citations", []) if isinstance(c, int) and 1 <= c <= n]
        text = claim.get("text", "")
        entry = {"claim": i, "text": text, "citations": cites, "lexical_support": False}
        if not cites:
            v.notes.append(f"Claim {i} has no valid passage citation: \"{text[:90]}\"")
        else:
            ct = _terms(text)
            best = 0.0
            for c in cites:
                pt = _terms(packet.chunks[c - 1].text)
                if ct:
                    best = max(best, len(ct & pt) / len(ct))
            # The overlap score is a backstop, not the decision. A plain-language
            # paraphrase of policy text legitimately shares few words with it
            # ("fewer or less severe headaches" vs "positive clinical response"),
            # so only a near-zero overlap fails here; anything else goes to the
            # judge, which is the right tool for paraphrase support.
            entry["lexical_support"] = best >= LEXICAL_HARD_FLOOR
            entry["overlap"] = round(best, 2)
            if best < LEXICAL_HARD_FLOOR and not text.startswith("[dry-run]"):
                v.notes.append(f"Claim {i} shares almost no words with its cited passage(s) {cites}; "
                               f"cite the passage that actually says this or drop the claim: \"{text[:90]}\"")
        v.per_claim.append(entry)
    v.passed = not v.notes
    return v


JUDGE_SYSTEM = (
    "You are a strict reviewer. For each numbered claim you are given the passage(s) it cites. "
    "Decide whether the passage text SUPPORTS the claim as written. Reply with a JSON array of objects "
    "{\"claim\": n, \"verdict\": \"SUPPORTED\" | \"PARTIAL\" | \"NOT_SUPPORTED\", \"reason\": \"...\"} and nothing else. "
    "PARTIAL means the passage supports part of the claim but the claim adds something the passage does not say. "
    "Do not use outside knowledge; only the passage text counts."
)


def llm_judge(answer: dict, packet: ContextPacket) -> tuple[list[dict], llm.LLMResult]:
    items = []
    for i, claim in enumerate(answer.get("claims", []), start=1):
        cites = [c for c in claim.get("citations", []) if isinstance(c, int) and 1 <= c <= len(packet.chunks)]
        passages = "\n---\n".join(f"[Passage {c}] {packet.chunks[c - 1].text}" for c in cites)
        items.append(f"CLAIM {i}: {claim.get('text', '')}\nCITED PASSAGES:\n{passages}")
    user = "\n\n=====\n\n".join(items)
    stub = json.dumps([{"claim": i + 1, "verdict": "SUPPORTED", "reason": "dry-run"} for i in range(len(items))])
    res = llm.complete("small", JUDGE_SYSTEM, user, max_tokens=1500, dry_run_stub=stub)
    parsed = None
    try:
        m = re.search(r"\[.*\]", res.text, flags=re.DOTALL)
        parsed = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        parsed = None
    return (parsed or []), res


def verify(answer: dict | None, packet: ContextPacket, use_judge: bool = True) -> Verdict:
    """Code checks, then the judge. Every per_claim entry ends with a
    `failed` flag so the pipeline can prune individual claims rather than
    redraft the whole answer."""
    v = code_checks(answer, packet)
    if not answer or not isinstance(answer, dict) or not v.per_claim:
        return v
    for entry in v.per_claim:
        entry["failed"] = (not entry["citations"]) or (not entry["lexical_support"])
    if use_judge and answer.get("claims"):
        verdicts, res = llm_judge(answer, packet)
        v.judge = res
        for item in verdicts:
            idx = item.get("claim")
            if not (isinstance(idx, int) and 1 <= idx <= len(v.per_claim)):
                continue
            verdict = item.get("verdict", "SUPPORTED")
            v.per_claim[idx - 1]["judge"] = verdict
            if verdict != "SUPPORTED":
                v.per_claim[idx - 1]["failed"] = True
                v.notes.append(f"Claim {idx} judged {verdict}: {item.get('reason', '')}")
    v.passed = not v.notes
    return v
