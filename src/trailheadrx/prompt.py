"""Prompt builder: the only component that calls the model to draft.

It combines four things into one call:
  1. the system prompt — the rules block from governance/rules.yaml, verbatim;
  2. the context packet — numbered passages with citation headers;
  3. the question type from the router;
  4. the answer schema — the JSON shape the model must fill, so every claim
     carries passage numbers the verifier can check.

On a failed verification it is called again with the verifier's notes
appended (the "revise" arrow). The router lives here too because it is a
tiny model call with its own prompt.

FLUENCY.md: "prompt assembly", "system prompt", "structured output", "routing".
"""
from __future__ import annotations

import json

from . import config, llm
from .retrieve import ContextPacket
from .rules import rules_text

QUESTION_TYPES = ["lookup", "compare_routes", "checklist", "out_of_scope"]

ROUTER_SYSTEM = (
    "You classify a patient's question about a migraine medicine and their health plan into exactly one type. "
    "Reply with one word from this list and nothing else: lookup, compare_routes, checklist, out_of_scope.\n"
    "lookup = what does my plan require / cover / step therapy / prior authorization / appeal.\n"
    "compare_routes = cost or the cheapest or best way to get the medicine, manufacturer programs, copay cards, discount cards.\n"
    "checklist = what my doctor needs to submit or document.\n"
    "out_of_scope = anything else, including which drug is best."
)


def route(question: str) -> tuple[str, llm.LLMResult]:
    """Classify the question. Dry-run falls back to keyword rules."""
    q = question.lower()
    if any(w in q for w in ("cost", "cheap", "price", "copay card", "coupon", "discount", "afford", "pay")):
        stub = "compare_routes"
    elif any(w in q for w in ("doctor need", "submit", "document", "checklist", "paperwork", "office need")):
        stub = "checklist"
    else:
        stub = "lookup"
    res = llm.complete("small", ROUTER_SYSTEM, question, max_tokens=10, dry_run_stub=stub)
    label = res.text.strip().lower().split()[0] if res.text.strip() else "lookup"
    if label not in QUESTION_TYPES:
        label = "lookup"
    return label, res


# The answer schema. Claims are atomic statements, each with passage numbers.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Two or three plain sentences a patient can read first. Start with the answer, not the background."},
        "prior_authorization_required": {"type": ["boolean", "null"]},
        "wait_estimate": {
            "type": "object",
            "description": "If the passages require trials of other medicines first, estimate how long the whole path could take for someone starting from zero: add the required trial lengths as if done one after another, plus a couple of weeks for the plan's review. Null if the passages give no trial lengths.",
            "properties": {
                "months_low": {"type": ["integer", "null"]},
                "months_high": {"type": ["integer", "null"]},
                "explanation": {"type": "string", "description": "One plain sentence on how the estimate was built, e.g. 'two 2-month tries plus the plan review'."},
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": [
                        "prior_authorization", "step_therapy", "trial_definition", "diagnosis_requirements",
                        "documentation", "prescriber", "quantity_or_dose_limits", "initial_approval_period",
                        "reauthorization", "exclusions_or_combinations", "timeline", "appeal", "variation", "other"]},
                    "text": {"type": "string", "description": "One plain-language statement."},
                    "citations": {"type": "array", "items": {"type": "integer"}, "description": "Passage numbers that support this statement."},
                },
                "required": ["topic", "text", "citations"],
            },
        },
        "not_in_documents": {"type": "array", "items": {"type": "string"},
                             "description": "Parts of the question the passages do not answer."},
    },
    "required": ["summary", "claims", "not_in_documents"],
}


def build_system_prompt() -> str:
    return rules_text() + (
        "\n\nOutput format: reply with a single JSON object matching this schema and nothing else:\n"
        + json.dumps(ANSWER_SCHEMA, indent=1)
        + "\n\nEach item in `claims` is ONE statement with the passage numbers that support it. "
          "Prefer several short claims over one long one. Put anything the passages do not cover in "
          "`not_in_documents` instead of guessing.\n\n"
          "Writing style (this matters as much as accuracy): write like a helpful friend who happens to know "
          "insurance, not like the policy. Say 'your health plan', never 'the plan' or 'the policy'. Say a medicine "
          "'didn't work or caused side effects', never 'failed' or 'failed trial'. Say 'your doctor', not 'the "
          "prescriber'. Say 'approval' for prior authorization, and explain the term once in parentheses the first "
          "time only. No policy jargon without a plain phrase in its place; drug names are fine. Short sentences. "
          "Speak to the reader as 'you'. Lead with what they have to do, not with what the policy is called.\n\n"
          "Already handled elsewhere (do NOT list these under not_in_documents): Ohio's step-therapy exception law, "
          "manufacturer copay-card eligibility, patient assistance income rules, and manufacturer direct-purchase "
          "options. Those are added to the answer by code from separate sources.\n\n"
          "Advocacy: Trailhead Rx is on the patient's side. If the passages show that the same policy treats "
          "some members differently — by state, plan type, age, or strength — say so plainly in a claim with "
          "topic `variation`, name which members get the lighter requirement, and note that such differences "
          "usually come from state law, not from anything about the patient. If the passages describe an "
          "exception or exemption process, state it as something the patient can ask for."
    )


def build_user_prompt(packet: ContextPacket, question_type: str, revision_notes: list[str] | None = None) -> str:
    drug = packet.drug.get("brand") if packet.drug else packet.plan_match.drug
    pm = packet.plan_match
    parts = [
        f"Plan: {pm.payer_query} ({pm.line_of_business}). Medicine: {drug}.",
        f"Question type: {question_type}.",
        f"Patient's question: {packet.question}",
        "",
        "Passages from the plan's published policy documents:",
        packet.as_prompt_text(),
    ]
    if revision_notes:
        parts += ["", "A reviewer rejected the previous draft for these reasons. Fix every one:",
                  *[f"- {n}" for n in revision_notes]]
    return "\n".join(parts)


def _dry_run_draft(packet: ContextPacket) -> str:
    """A stand-in draft that cites real passages, so the pipeline can be tested
    without a key. Clearly labeled; never shown to a patient."""
    claims = []
    for i, c in enumerate(packet.chunks[:4], start=1):
        first = c.text.strip().split("\n")[0][:160]
        claims.append({"topic": "other", "text": f"[dry-run] {first}", "citations": [i]})
    return json.dumps({
        "summary": "[dry-run] This is a stand-in answer generated without a model call.",
        "prior_authorization_required": None,
        "claims": claims,
        "not_in_documents": [],
    })


def draft(packet: ContextPacket, question_type: str, revision_notes: list[str] | None = None) -> tuple[dict | None, llm.LLMResult, str]:
    """Call the strong model. Returns (parsed answer or None, raw result, system prompt used)."""
    system = build_system_prompt()
    user = build_user_prompt(packet, question_type, revision_notes)
    res = llm.complete("strong", system, user, dry_run_stub=_dry_run_draft(packet))
    parsed = llm.extract_json(res.text)
    return parsed, res, system
