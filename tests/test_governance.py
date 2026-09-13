"""Rules-as-code and guardrails are ordinary code, so they get ordinary tests.
Every rule in governance/rules.yaml that says "enforced by code" has at
least one test here. No model calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trailheadrx import rules
from trailheadrx.guardrails import phi_check, scope_check
from trailheadrx.ingest import chunk, Page


# ---- G4: eligibility rules-as-code -----------------------------------------

def test_copay_card_blocked_for_federal_programs():
    for lob in ("medicare_advantage", "medicaid_mco", "medicaid_ffs"):
        assert rules.copay_card_allowed(lob).allowed is False
    assert rules.copay_card_allowed("commercial").allowed is True


def test_ohio_step_therapy_law_scope():
    assert rules.ohio_step_therapy_law_applies("medicare_advantage", None).allowed is False
    assert rules.ohio_step_therapy_law_applies("commercial", True).allowed is False   # self-funded ERISA
    assert rules.ohio_step_therapy_law_applies("commercial", False).allowed is True
    assert rules.ohio_step_therapy_law_applies("medicaid_mco", None).allowed is True
    unknown = rules.ohio_step_therapy_law_applies("commercial", None)
    assert unknown.allowed is True and "self-funded" in unknown.reason


def test_pap_income_rule():
    assert rules.pap_income_eligible(None).allowed is True          # unknown → state the rule
    assert rules.pap_income_eligible(3.0, 4.0).allowed is True
    assert rules.pap_income_eligible(5.0, 4.0).allowed is False


# ---- G5: PHI detection -----------------------------------------------------

def test_phi_patterns_refuse():
    bad = [
        "my SSN is 123-45-6789 what does my plan require",
        "DOB 04/12/1981, need Emgality",
        "member id: AB12345678 does Aetna cover Nurtec",
        "call me at (614) 555-0142",
        "email me at someone@example.com",
        "I was diagnosed with chronic migraine last year, what now",
        "my neurologist said I need Emgality",
    ]
    for text in bad:
        r = phi_check(text)
        assert r.passed is False, text
        # The refusal reason must not echo the sensitive text back.
        assert "123-45-6789" not in r.reason and "AB12345678" not in r.reason


def test_clean_questions_pass():
    for text in [
        "What does UnitedHealthcare require before covering Emgality?",
        "Do I have to try other medicines first?",
        "How long does a decision take and how do I appeal?",
    ]:
        assert phi_check(text).passed is True


# ---- G1: scope / advice ----------------------------------------------------

def test_scope_check():
    assert scope_check("Emgality", "what does the plan require").passed
    assert scope_check("Ozempic", "what does the plan require").passed is False   # not a migraine drug
    assert scope_check("Emgality", "which drug is best for me").passed is False
    assert scope_check("Emgality", "should I switch to Ajovy").passed is False


# ---- Chunking keeps criteria together --------------------------------------

def test_chunker_respects_headings_and_pages():
    pages = [Page(1, "1. Background\n\n" + ("word " * 300) + "\n\n2. Coverage Criteria\n\nAll of the following: a b c."),
             Page(2, "3. Reauthorization\n\nPositive response required.")]
    chunks = chunk(pages)
    assert len(chunks) >= 2
    assert all(c.page_start <= c.page_end for c in chunks)
    sections = {c.section for c in chunks}
    assert any("Coverage Criteria" in s for s in sections)



# ---- Programs corpus → other routes (rules-as-code applied) ----------------

def test_other_routes_commercial_vs_medicare():
    from trailheadrx.programs import other_routes
    com = "\n".join(other_routes("Emgality", "commercial"))
    med = "\n".join(other_routes("Emgality", "medicare_advantage"))
    assert "LillyDirect" in com and "$35" in com and "Lilly Cares" in com
    assert "not available to you" in med and "$35" not in med.split("not available")[0]
    assert "Lilly Cares" in med                      # assistance still shown for Medicare
    assert "Reyvow" not in com


def test_other_routes_discontinued_and_unknown():
    from trailheadrx.programs import other_routes
    assert "discontinued" in other_routes("Reyvow", "commercial")[0]
    assert "on file" in other_routes("Ozempic", "commercial")[0]
