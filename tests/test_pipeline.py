"""End-to-end in dry-run: ingestion → retrieval → draft (stub) → verify →
output guardrails → audit. Proves the plumbing without a model call."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_index_has_chunks(indexed):
    assert indexed["chunks"] >= 2


def test_plan_match_and_retrieval(indexed):
    from trailheadrx.retrieve import plan_match, hybrid_retrieve
    pm = plan_match("UnitedHealthcare", "commercial", "Emgality")
    assert pm.confidence >= 0.6, pm.reason
    chunks = hybrid_retrieve("What do I have to try before Emgality is covered?", pm)
    assert chunks, "retrieval returned nothing"
    joined = " ".join(c.text for c in chunks).lower()
    assert "two of the following" in joined or "at least two" in joined
    assert all(c.citation() for c in chunks)


def test_abstains_when_pair_not_in_corpus(indexed):
    from trailheadrx.pipeline import answer
    a = answer("What is required?", "Anthem", "commercial", "Emgality")
    assert a.outcome == "abstained"
    assert a.reason


def test_refuses_phi_and_logs_only_reason(indexed):
    from trailheadrx.pipeline import answer
    from trailheadrx.audit import read_all
    a = answer("my SSN is 123-45-6789, what is required", "UnitedHealthcare", "commercial", "Emgality")
    assert a.outcome == "refused"
    records = read_all()
    last = records[-1]
    assert last["outcome"] == "refused"
    # The audit log must not contain the refused text.
    import json
    assert "123-45-6789" not in json.dumps(last)


def test_answers_in_dry_run_with_citations(indexed):
    from trailheadrx.pipeline import answer
    a = answer("What do I have to try before Emgality is covered?", "UnitedHealthcare", "commercial", "Emgality",
               self_funded=False)
    assert a.outcome == "answered", a.reason
    assert a.dry_run is True
    assert a.claims and all(c["citations"] for c in a.claims)
    assert a.citations
    assert any("copay" in e["reason"].lower() for e in a.eligibility)
    assert a.framing


def test_output_guardrail_drops_uncited_claims(indexed):
    from trailheadrx.retrieve import build_packet
    from trailheadrx.guardrails import output_guardrails
    from trailheadrx.rules import rules_text
    packet = build_packet("what is required", "UnitedHealthcare", "commercial", "Emgality", rules_text())
    draft = {"summary": "ok", "claims": [
        {"topic": "step_therapy", "text": "Two preventives required.", "citations": [1]},
        {"topic": "other", "text": "Made up with no source.", "citations": []},
        {"topic": "other", "text": "Cites a passage that does not exist.", "citations": [99]},
    ], "not_in_documents": []}
    final, gate = output_guardrails(draft, packet)
    assert gate.passed
    assert len(final["claims"]) == 1   # uncited and mis-cited claims are dropped silently here;
    # the pipeline reports verifier-pruned claims in its own "what we left out" section.


def test_output_guardrail_blocks_advice(indexed):
    from trailheadrx.retrieve import build_packet
    from trailheadrx.guardrails import output_guardrails
    from trailheadrx.rules import rules_text
    packet = build_packet("what is required", "UnitedHealthcare", "commercial", "Emgality", rules_text())
    draft = {"summary": "You should switch to Ajovy.", "claims": [
        {"topic": "other", "text": "Two preventives required.", "citations": [1]}], "not_in_documents": []}
    _, gate = output_guardrails(draft, packet)
    assert gate.passed is False
