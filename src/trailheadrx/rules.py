"""Rules-as-code: the eligibility engine.

These are the decisions we refuse to delegate to the model. Each function is
ordinary code with an ordinary test, takes plain inputs, and returns a
Decision with a reason the answer can show the patient. The verifier calls
these; the prompt builder renders their output into the answer; the model
never sees the question "is this patient eligible?".

FLUENCY.md: "rules-as-code".
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from . import config


@dataclass
class Decision:
    allowed: bool
    rule_id: str
    reason: str


def _elig() -> dict:
    with open(config.path("rules"), encoding="utf-8") as f:
        return yaml.safe_load(f)["eligibility"]


def rules_text() -> str:
    """The rules block inserted verbatim into every system prompt."""
    with open(config.path("rules"), encoding="utf-8") as f:
        return yaml.safe_load(f)["system_prompt_rules"]


def all_rules() -> list[dict]:
    with open(config.path("rules"), encoding="utf-8") as f:
        return yaml.safe_load(f)["rules"]


def copay_card_allowed(line_of_business: str) -> Decision:
    """Manufacturer copay cards and federal program members."""
    blocked = _elig()["copay_card_blocked_lobs"]
    if line_of_business in blocked:
        return Decision(False, "G4",
                        "Because your coverage is through Medicare or Medicaid, federal rules do not let you "
                        "use the drug maker's copay card. The maker's free-medicine program may still be an option.")
    return Decision(True, "G4", "With employer or commercial coverage, you can usually use the drug maker's copay card "
                                "to lower what you pay at the pharmacy. Each maker sets its own terms.")


def ohio_step_therapy_law_applies(line_of_business: str, self_funded: bool | None) -> Decision:
    """Whether ORC 3901.832 (step therapy exemption rights) protects this plan."""
    e = _elig()
    if line_of_business not in e["ohio_step_therapy_law_lobs"]:
        return Decision(False, "G4",
                        "Ohio's step-therapy law does not cover Medicare plans, so you would use your plan's "
                        "own exception and appeal process instead.")
    # Self-funding is only a question for employer (commercial) coverage.
    if line_of_business not in ("commercial", "marketplace"):
        self_funded = False
    if e["ohio_step_therapy_law_excludes_self_funded"] and self_funded:
        return Decision(False, "G4",
                        "Your employer plan appears to be self-funded, which puts it under federal rather than "
                        "Ohio rules, so Ohio's step-therapy law does not apply. Your plan may still have its "
                        "own way to ask for an exception.")
    if e["ohio_step_therapy_law_excludes_self_funded"] and self_funded is None:
        return Decision(True, "G4",
                        "Ohio law likely gives you the right to ask your health plan to skip these steps — for "
                        "example if you already tried one of the medicines on another plan, or it is unsafe for "
                        "you. The plan must answer within 10 days (2 days if urgent). One catch: if your "
                        "employer plan is self-funded, this law does not apply; HR can tell you.")
    return Decision(True, "G4",
                    "Ohio law gives you the right to ask your health plan to skip these steps — for example "
                    "if you already tried one of the medicines on another plan, or it is unsafe for you. "
                    "The plan must answer within 10 days (2 days if urgent).")


def pap_income_eligible(household_fpl_multiple: float | None, program_max_multiple: float | None = None) -> Decision:
    """Patient assistance programs use an income ceiling as a multiple of the
    federal poverty level. We never ask for income in V1; this returns the
    rule so the answer can state it, and evaluates only if a value is given."""
    ceiling = program_max_multiple or _elig()["pap_default_fpl_multiple"]
    if household_fpl_multiple is None:
        return Decision(True, "G4",
                        f"If cost is the problem, most drug makers have a free-medicine program for people with "
                        f"household income under about {ceiling:g} times the federal poverty level.")
    if household_fpl_multiple <= ceiling:
        return Decision(True, "G4", f"Income is within the program's ceiling ({ceiling:g}× FPL).")
    return Decision(False, "G4", f"Income appears above the program's ceiling ({ceiling:g}× FPL).")


def eligibility_summary(line_of_business: str, self_funded: bool | None = None) -> list[Decision]:
    """The decisions that accompany every answer for a given plan type."""
    # Copay-card and assistance specifics now come from the programs corpus
    # (programs.py); this summary keeps the plan-type rules a patient must know.
    return [
        ohio_step_therapy_law_applies(line_of_business, self_funded),
        copay_card_allowed(line_of_business),
    ]
