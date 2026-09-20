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
        "my member id is AB12345678, what do I need?",
        "my subscriber number's 99887766 and I need Emgality",
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


def test_assistance_program_eligibility_is_rules_as_code():
    from trailheadrx import rules
    from trailheadrx.programs import routes_structured
    never = {"eligible_coverage": {"commercial": "never", "medicare": True, "medicaid": False}}
    cond = {"eligible_coverage": {"commercial": "if_not_covered", "medicare": True, "medicaid": False}}
    assert rules.assistance_allowed("commercial", never).allowed is False
    assert rules.assistance_allowed("commercial", cond).allowed is True
    assert rules.assistance_allowed("medicare_advantage", never).allowed is True
    assert rules.assistance_allowed("medicaid_mco", cond).allowed is False
    assert rules.assistance_allowed("commercial", {}).allowed is True      # unknown → say so, do not hide
    # applied to the corpus: Lilly Cares is not open to commercial members, is to Medicare
    pap = {r["key"]: r for r in routes_structured("Emgality", "commercial")}["pap"]
    assert pap["available"] is False and "no insurance or on Medicare" in pap["reason"]
    pap = {r["key"]: r for r in routes_structured("Emgality", "medicare_advantage")}["pap"]
    assert pap["available"] is True


def test_lead_time_is_computed_in_code_from_trial_lengths():
    from trailheadrx.compare import months_from_steps
    assert months_from_steps([{"what": "a preventive", "days": 56}]) == (2, 3, 56)
    assert months_from_steps([{"what": "a", "days": 56}, {"what": "b", "days": 56}]) == (4, 5, 112)
    assert months_from_steps([{"what": "a", "days": 90}]) == (3, 4, 90)
    assert months_from_steps([{"what": "a", "days": None}]) == (None, None, 0)   # no length stated → no estimate
    assert months_from_steps([]) == (None, None, 0)


def test_refresh_fingerprint_ignores_html_noise_and_reindexes(indexed, tmp_path, monkeypatch):
    from trailheadrx import refresh
    a = b"<html><head><script>var nonce='abc'</script></head><body><p>Step therapy: try two preventives.</p></body></html>"
    b = b"<html><head><script>var nonce='xyz'</script></head><body><p>Step  therapy: try two preventives.</p></body></html>"
    c = b"<html><body><p>Step therapy: try ONE preventive.</p></body></html>"
    assert refresh._fingerprint(a, "html") == refresh._fingerprint(b, "html")     # script noise + whitespace ignored
    assert refresh._fingerprint(a, "html") != refresh._fingerprint(c, "html")     # real change seen
    # state file lives where TRAILHEADRX_REFRESH_STATE points
    monkeypatch.setenv("TRAILHEADRX_REFRESH_STATE", str(tmp_path / "state.json"))
    refresh.save_state({"https://x": {"kind": "policy", "file": "f.html", "checked": "2026-09-21"}})
    assert refresh.last_checked() == "2026-09-21"
    # remove_document really empties the index for that file
    from trailheadrx.ingest import remove_document
    from trailheadrx.retrieve import open_db
    conn = open_db()
    row = dict(conn.execute("SELECT payer,line_of_business,benefit_type,scope,title,policy_id,url,"
                            "effective_or_reviewed,file,downloaded_on,status FROM documents LIMIT 1").fetchone())
    before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n = remove_document(conn, row["file"])
    conn.commit()
    assert n > 0 and conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == before - n
    conn.close()
    # put it back exactly as it was so later tests still have their fixture
    from trailheadrx.ingest import ingest
    ingest(documents=[row])


def test_refresh_email_is_optional(monkeypatch):
    from trailheadrx import refresh
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert refresh.send_email("s", "b") is False


def test_lookback_window_is_not_a_trial_length():
    """Aetna 3481-E: 'a 56-day supply of one preventive in the past 730 days'.
    The model once returned the 730-day window as a step, and the grid showed
    Nurtec at 30–32 months (fix41). A step longer than any real try is treated
    as unstated, not summed."""
    from trailheadrx.compare import months_from_steps
    lo, hi, days = months_from_steps([{"what": "one preventive", "days": 56}, {"what": "look-back", "days": 730}])
    assert (lo, hi, days) == (2, 3, 56)
    lo, hi, days = months_from_steps([{"what": "two triptans", "days": 180}, {"what": "window", "days": 730}])
    assert days == 180 and hi <= 8
    assert months_from_steps([{"what": "window only", "days": 730}]) == (None, None, 0)


def test_payer_match_ignores_place_and_generic_words():
    from trailheadrx.retrieve import _payer_matches
    assert _payer_matches("Medical Mutual of Ohio", "Medical Mutual of Ohio")
    assert not _payer_matches("Medical Mutual of Ohio", "Anthem / Elevance (Ohio) — CarelonRx")
    assert not _payer_matches("Medical Mutual of Ohio", "Anthem / Elevance — CarelonRx Medical Drug")
    assert _payer_matches("Aetna (CVS Caremark criteria)", "Aetna")
    assert _payer_matches("Aetna", "Aetna (CVS Caremark criteria)")
    assert _payer_matches("UnitedHealthcare", "UnitedHealthcare / Optum Rx")
    assert not _payer_matches("Humana (Part D)", "UnitedHealthcare / Optum Rx")
    assert _payer_matches("CareSource (Ohio Medicaid)", "CareSource (Ohio Medicaid)")
