"""The request pipeline: one function that walks a question through every box
in docs/ARCHITECTURE.md, in order, and returns an Answer.

  input guardrails → plan match → hybrid retrieval → router → prompt builder
  → model → verifier (→ revise, up to N times) → output guardrails → audit log

Every early exit is an abstention with a reason, not an error. Abstentions
are logged the same way as answers because the evals score them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from . import config
from .audit import Trace
from .guardrails import input_guardrails, output_guardrails
from .prompt import route, draft
from .retrieve import build_packet
from .rules import rules_text, eligibility_summary
from .verify import verify


HOW_TO_USE = (
    "This is a plain-words reading of your health plan's own published rules. It is not the final word on your "
    "case, and it is not medical advice. Think of it as a map for the conversation with your doctor's office and "
    "your health plan: it tells you what they will ask for, how long it may take, and where you have room to push. "
    "That is also why we show things like how the same plan treats people in other states, and faster ways to get "
    "the medicine your doctor thinks is right for you. Your doctor's office sends in the request; your health plan "
    "makes the decision; you can ask questions and ask for exceptions at every step."
)

@dataclass
class Answer:
    outcome: str                      # answered | abstained | refused | blocked
    reason: str = ""
    summary: str = ""
    claims: list[dict] = field(default_factory=list)
    not_in_documents: list[str] = field(default_factory=list)
    eligibility: list[dict] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    framing: str = ""
    question_type: str = ""
    trace_id: str = ""
    dry_run: bool = False
    wait_estimate: dict | None = None
    other_routes: list[str] = field(default_factory=list)
    drug: str = ""
    left_out: list[dict] = field(default_factory=list)   # claims dropped by the verifier: shown, labeled unconfirmed

    def render(self) -> str:
        """Plain-text rendering for the CLI, laid out for a person, not a reviewer:
        the short version first, then what to do, grouped under everyday headings.
        Citations stay (they are the trust), but as small trailing markers, with
        the source list at the very end."""
        if self.outcome != "answered":
            lines = [self.reason, ""]
            if self.eligibility:
                lines += ["Good to know for your kind of plan:"] + [f"  • {e['reason']}" for e in self.eligibility] + [""]
            lines += ["HOW TO USE THIS", "  " + (self.framing or HOW_TO_USE)]
            lines.append(f"\n(ref {self.trace_id}{'; dry run' if self.dry_run else ''})")
            return "\n".join(lines)

        headings = [
            ("What you need to try first", {"step_therapy", "trial_definition"}),
            ("What your doctor needs to show", {"prior_authorization", "documentation", "prescriber", "diagnosis_requirements"}),
            ("Who gets an easier path", {"variation"}),
            ("Once you're approved", {"initial_approval_period", "reauthorization", "quantity_or_dose_limits", "exclusions_or_combinations"}),
            ("Timing and appeals", {"timeline", "appeal"}),
            ("Also worth knowing", {"other"}),
        ]
        def cite(c):
            return "  " + "".join(f"[{n}]" for n in c.get("citations", []))

        lines = ["THE SHORT VERSION", self.summary or ""]
        if self.wait_estimate and self.wait_estimate.get("months_high"):
            lo, hi = self.wait_estimate.get("months_low"), self.wait_estimate.get("months_high")
            rng = f"about {hi} months" if not lo or lo == hi else f"about {lo} to {hi} months"
            lines += ["", f"If you are starting from zero, the path your plan describes could take {rng} "
                          f"before {self.drug or 'the medicine'} is approved ({self.wait_estimate.get('explanation', '').rstrip('.')}). "
                          f"It can be shorter if you have already tried some of these medicines, or if you qualify for an exception."]
        for title, topics in headings:
            group = [c for c in self.claims if c.get("topic", "other") in topics]
            if not group:
                continue
            lines += ["", title.upper()]
            lines += [f"  • {c.get('text', '')}{cite(c)}" for c in group]
        if self.other_routes:
            lines += ["", "OTHER WAYS TO GET IT"] + [f"  • {r}" for r in self.other_routes]
        if self.eligibility:
            lines += ["", "GOOD TO KNOW FOR YOUR KIND OF PLAN"] + [f"  • {e['reason']}" for e in self.eligibility]
        if self.not_in_documents:
            lines += ["", "YOUR PLAN'S DOCUMENTS DON'T SAY"] + [f"  • {x}" for x in self.not_in_documents]
        if self.left_out:
            n = len(self.left_out)
            lines += ["", "WHAT WE LEFT OUT, AND WHY",
                      f"  Everything above is backed by a page of your plan's documents you can check. We wrote "
                      f"{'one more statement' if n == 1 else f'{n} more statements'} but could not match "
                      f"{'it' if n == 1 else 'them'} to the documents closely enough to stand behind "
                      f"{'it' if n == 1 else 'them'}, so {'it is' if n == 1 else 'they are'} listed here as "
                      f"unconfirmed. Nothing above depends on {'it' if n == 1 else 'them'}; if one matters to you, "
                      f"it is a good question for your doctor's office."]
            lines += [f"  • Unconfirmed: {c.get('text', '')}" for c in self.left_out]
        if self.warnings:
            lines += ["", "ONE MORE THING"] + [f"  • {w}" for w in self.warnings]
        lines += ["", "HOW TO USE THIS", "  " + HOW_TO_USE]
        if self.citations:
            lines += ["", "WHERE THIS COMES FROM"] + [f"  [{i}] {c}" for i, c in enumerate(self.citations, start=1)]
        lines.append(f"\n(ref {self.trace_id}{'; dry run' if self.dry_run else ''})")
        return "\n".join(lines)


def answer(question: str, payer: str, line_of_business: str, drug: str, self_funded: bool | None = None,
           use_judge: bool = True) -> Answer:
    # The question is NOT placed in the trace until the input guardrails pass:
    # a refused question may contain the very thing we must not store.
    trace = Trace({"question": None, "payer": payer, "line_of_business": line_of_business,
                   "drug": drug, "self_funded": self_funded})
    dry = config.dry_run()

    # 1. Input guardrails
    gate = input_guardrails(drug, question)
    if not gate.passed:
        trace.stage("input_guardrails", passed=False, reason=gate.reason)
        trace.finish("refused", reason=gate.reason)
        return Answer("refused", gate.reason, trace_id=trace.id, dry_run=dry)
    trace.record["request"]["question"] = question
    trace.stage("input_guardrails", passed=True)

    # 2–3. Plan match + retrieval → context packet
    packet = build_packet(question, payer, line_of_business, drug, rules_text())
    pm = packet.plan_match
    trace.stage("plan_match", confidence=pm.confidence, reason=pm.reason,
                documents=[d.get("title") or d.get("payer") for d in pm.documents], indexed=pm.indexed_files)
    if pm.confidence < config.CONFIG["thresholds"]["plan_match_min_confidence"] or not packet.chunks:
        reason = pm.reason if pm.confidence < config.CONFIG["thresholds"]["plan_match_min_confidence"] \
            else "The governing document is indexed but no passage matched the question."
        trace.stage("retrieval", chunks=0)
        trace.finish("abstained", reason=reason)
        elig = [asdict(d) for d in eligibility_summary(line_of_business, self_funded)]
        return Answer("abstained", reason, eligibility=elig, trace_id=trace.id, dry_run=dry,
                      framing="Trailhead Rx only answers from documents it has. When it does not have the right one, it says so.")
    trace.stage("retrieval", chunks=len(packet.chunks), packet=packet.to_dict()["chunks"])

    # 4. Router
    qtype, rres = route(question)
    trace.call("router", rres)
    trace.stage("router", question_type=qtype)
    if qtype == "out_of_scope":
        trace.finish("refused", reason="out of scope")
        return Answer("refused", "That question is outside what Trailhead Rx answers (what the plan's policy says).",
                      question_type=qtype, trace_id=trace.id, dry_run=dry)

    # 5–6. Draft → verify → (prune | revise)
    # A failed claim is dropped, not redrafted, as long as most of the answer
    # verified: pruning converges, regeneration does not (each redraft brings
    # a fresh borderline claim). Redraft only for broken JSON or a majority failure.
    notes: list[str] = []
    parsed = None
    verdict = None
    pruned: list[str] = []
    for attempt in range(1, config.CONFIG["thresholds"]["max_revisions"] + 2):
        parsed, dres, _ = draft(packet, qtype, notes or None)
        trace.call(f"draft_{attempt}", dres)
        verdict = verify(parsed, packet, use_judge=use_judge)
        if verdict.judge:
            trace.call(f"judge_{attempt}", verdict.judge)
        trace.stage(f"verify_{attempt}", passed=verdict.passed, notes=verdict.notes, per_claim=verdict.per_claim)
        if verdict.passed:
            break
        failed_idx = [e["claim"] for e in verdict.per_claim if e.get("failed")]
        total = len(verdict.per_claim)
        if parsed and total and len(failed_idx) <= max(1, total // 2) and total - len(failed_idx) >= 2:
            keep = [c for i, c in enumerate(parsed.get("claims", []), start=1) if i not in failed_idx]
            pruned = [{"topic": parsed["claims"][i - 1].get("topic", "other"),
                       "text": parsed["claims"][i - 1].get("text", "")} for i in failed_idx]
            parsed["claims"] = keep
            trace.stage(f"prune_{attempt}", dropped=len(failed_idx), kept=len(keep))
            verdict.passed = True
            break
        notes = verdict.notes
    if not verdict or not verdict.passed:
        trace.finish("abstained", reason="The draft could not be verified against the policy passages after revision.")
        return Answer("abstained", "I could not produce an answer where every statement is supported by the "
                      "policy documents, so I am not giving one.", trace_id=trace.id, dry_run=dry)

    # 7. Output guardrails
    final, ogate = output_guardrails(parsed, packet)
    # Pruned claims are reported in their own section, not as a warning.
    trace.stage("output_guardrails", passed=ogate.passed, reason=ogate.reason, warnings=ogate.warnings)
    if not ogate.passed:
        trace.finish("blocked", reason=ogate.reason)
        return Answer("blocked", ogate.reason, trace_id=trace.id, dry_run=dry)

    elig = [asdict(d) for d in eligibility_summary(line_of_business, self_funded)]
    cites = [c.citation() for c in packet.chunks]
    trace.finish("answered", claims=len(final["claims"]))
    return Answer("answered", "", final.get("summary", ""), final["claims"], final.get("not_in_documents", []),
                  elig, cites, final.get("warnings", []), final.get("framing", ""), qtype, trace.id, dry,
                  wait_estimate=final.get("wait_estimate"), other_routes=other_routes(packet, line_of_business),
                  drug=(packet.drug or {}).get("brand", drug), left_out=pruned)


def other_routes(packet, line_of_business: str) -> list[str]:
    """A first version of Layer 2: what the drug list already knows about ways
    around coverage. Session 3 replaces these with verified program records
    (terms, eligibility, verified-on date). Until then every line says so."""
    rec = packet.drug or {}
    brand = rec.get("brand", packet.plan_match.drug)
    maker = rec.get("manufacturer", "the drug maker")
    programs = rec.get("programs") or {}
    out: list[str] = []
    if programs.get("dtc"):
        out.append(f"{maker} lists {brand} on its direct-purchase program ({programs['dtc'].split(' — ')[0]}), where you "
                   f"pay cash and skip the health plan entirely. That means no waiting on approvals or step therapy, but "
                   f"it also means nothing you pay counts toward your deductible, and a cash purchase does not count as "
                   f"a 'try' if you later go through your plan. Current price and terms: not verified yet.")
    if not out:
        out.append(f"No direct-purchase option for {brand} is on file yet. Session 3 adds verified maker programs.")
    return out
