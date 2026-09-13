"""Input and output guardrails. Checks at the edges, in code.

Input guardrails run before anything else and can refuse a request:
  - PHI detection: patterns and cues that suggest the person is typing
    identifiable or clinical detail about themselves or someone else.
    We refuse and store nothing — the audit log records only the reason.
  - Scope check: the drug must be one we cover; the question must be about
    coverage, not about which drug to take.

Output guardrails run after the verifier and can block, edit, or annotate:
  - citation check: any claim without a valid passage number is removed;
  - advice-language check: recommendation phrasing is blocked;
  - freshness: sources older than the threshold get a warning;
  - framing: every answer carries the "published policy, not a decision" line.

FLUENCY.md: "guardrails", "PHI detection", "groundedness", "abstention", "freshness".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import config
from .retrieve import resolve_drug


@dataclass
class GateResult:
    passed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Input guardrails
# --------------------------------------------------------------------------

PHI_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "a Social Security number"),
    (r"\b(?:\d{1,2}[/-]){2}(?:19|20)\d{2}\b", "a date of birth or date"),
    (r"\b(?:mrn|member id|member #|subscriber id|policy number)\s*[:#]?\s*[A-Z0-9-]{5,}\b", "a member or record ID"),
    (r"\b\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "a phone number"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "an email address"),
    (r"\b\d{1,5}\s+[A-Za-z0-9.\s]+\b(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|blvd)\b\.?", "a street address"),
]
CLINICAL_CUES = [
    r"\bmy (?:doctor|neurologist|pcp) (?:said|says|diagnosed|thinks)\b",
    r"\bi (?:was|am|have been) diagnosed\b",
    r"\bmy (?:diagnosis|mri|labs?|bloodwork|chart) (?:is|are|show|showed|says)\b",
    r"\bi (?:take|am taking|took) \d+\s?mg\b",
    r"\b(?:my|our) (?:son|daughter|wife|husband|mother|father|mom|dad)['’]?s? (?:diagnosis|condition|doctor said)\b",
    r"\bpatient name\b",
]


def phi_check(text: str) -> GateResult:
    """Refuse when the text looks like it carries identifiable or clinical
    detail. The matched text is never returned or stored — only the category."""
    if not text:
        return GateResult(True)
    for pat, label in PHI_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return GateResult(False, f"Your message looks like it includes {label}. Trailhead Rx does not "
                                     f"take or keep personal details — please ask about the plan and the "
                                     f"medicine only.")
    for pat in CLINICAL_CUES:
        if re.search(pat, text, flags=re.IGNORECASE):
            return GateResult(False, "Your message includes personal medical details. Trailhead Rx answers "
                                     "questions about what a plan's published policy says; it does not "
                                     "take or keep information about a person's health. Please rephrase "
                                     "without personal details.")
    return GateResult(True)


ADVICE_ASKS = [
    r"\bwhich (?:drug|medicine|medication|one) (?:is|works) (?:best|better)\b",
    r"\bshould i (?:take|switch|try|start|stop)\b",
    r"\bis .+ (?:better|safer|more effective) than\b",
    r"\bwhat dose\b|\bhow much should i take\b",
]


def scope_check(drug: str, question: str) -> GateResult:
    if not resolve_drug(drug):
        return GateResult(False, f"'{drug}' is not a migraine medicine Trailhead Rx covers yet. "
                                 f"The current list is in data/drugs_migraine.yaml.")
    for pat in ADVICE_ASKS:
        if re.search(pat, question, flags=re.IGNORECASE):
            return GateResult(False, "That is a question about which treatment is right for a person, "
                                     "which only the prescriber can answer. Trailhead Rx can explain what "
                                     "the plan requires for a specific medicine.")
    return GateResult(True)


def input_guardrails(drug: str, question: str) -> GateResult:
    r = phi_check(question)
    if not r.passed:
        return r
    return scope_check(drug, question)


# --------------------------------------------------------------------------
# Output guardrails
# --------------------------------------------------------------------------

ADVICE_LANGUAGE = [
    r"\byou should (?:take|switch to|try|start)\b",
    r"\bi recommend\b",
    r"\bthe best (?:drug|medicine|option) for you\b",
    r"\bworks better than\b",
]

FRAMING = ("This explains what your plan's published policy says. It is not a coverage decision "
           "and not medical advice. Your prescriber's office submits the request; your plan decides.")


def _parse_date(s: str) -> date | None:
    for m in re.finditer(r"(20\d{2})-(\d{2})-(\d{2})", s or ""):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
    return None


def freshness_warnings(chunks) -> list[str]:
    """One warning per source document whose date is older than the threshold.
    Policies are dated by effective/review date; the threshold is generous for
    policies (they change quarterly) and strict for program records (monthly)."""
    days = config.CONFIG["thresholds"]["freshness_days"]
    seen: set[str] = set()
    out: list[str] = []
    today = date.today()
    for c in chunks:
        if c.file in seen:
            continue
        seen.add(c.file)
        # A statute's effective date is when it became law, not a review date;
        # an old effective date is not staleness. Reference documents are
        # checked for currency at download time (manifest downloaded_on).
        if getattr(c, "line_of_business", "") == "reference":
            continue
        d = _parse_date(c.effective_or_reviewed) or _parse_date(getattr(c, "downloaded_on", "") or "")
        if d is None:
            out.append(f"We could not read the date on one of your plan's documents ('{c.title}'); it is worth "
                       f"checking that it is the current version.")
        elif (today - d).days > 365:
            out.append(f"One of your plan's documents ('{c.title}') is dated {d.isoformat()}, more than a year ago. "
                       f"Plans update these regularly, so ask whether there is a newer version.")
    return out


def output_guardrails(answer: dict, packet) -> tuple[dict, GateResult]:
    """Apply output rules to a verified answer. Returns (edited answer, gate)."""
    n = len(packet.chunks)
    dropped = 0
    for claim in answer.get("claims", []):
        good = [c for c in claim.get("citations", []) if isinstance(c, int) and 1 <= c <= n]
        if not good:
            claim["dropped"] = True
            dropped += 1
        claim["citations"] = good
    answer["claims"] = [c for c in answer.get("claims", []) if not c.get("dropped")]

    text_blob = " ".join(c.get("text", "") for c in answer["claims"]) + " " + answer.get("summary", "")
    for pat in ADVICE_LANGUAGE:
        if re.search(pat, text_blob, flags=re.IGNORECASE):
            return answer, GateResult(False, "The draft contained recommendation language (rule G1) and was blocked.")

    warnings = freshness_warnings(packet.chunks)
    if dropped:
        pass  # dropped claims are reported by the pipeline in the "what we left out" section
    answer["framing"] = FRAMING
    answer["warnings"] = warnings
    if not answer["claims"]:
        return answer, GateResult(False, "No statement in the draft could be tied to a policy passage; abstaining.")
    return answer, GateResult(True, warnings=warnings)
