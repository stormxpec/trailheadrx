"""Test setup: point the app at a temporary index and audit folder, and index
the synthetic fixture policy as if it were UnitedHealthcare's CGRP program.
No model calls happen in tests (no API key → dry-run).
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def indexed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("trx")
    os.environ["TRAILHEADRX_INDEX_DB"] = str(tmp / "index.sqlite")
    os.environ["TRAILHEADRX_AUDIT_DIR"] = str(tmp / "audit")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    from trailheadrx.ingest import ingest
    fixture = ROOT / "tests" / "fixtures" / "fixture-cgrp-policy.pdf"
    docs = [{
        "payer": "UnitedHealthcare / Optum Rx", "line_of_business": "commercial", "benefit_type": "pharmacy",
        "scope": "Aimovig, Ajovy, Emgality — PA / medical necessity",
        "title": "FIXTURE — CGRP Receptor Antagonists PA Program", "policy_id": "FIX-001",
        "url": "https://example.invalid/fixture.pdf", "effective_or_reviewed": "2026-07-01",
        "file": str(fixture), "downloaded_on": "2026-09-12", "status": "found",
    }]
    counts = ingest(rebuild=True, documents=docs)
    assert counts["documents"] == 1
    return counts
