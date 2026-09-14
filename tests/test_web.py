"""The website's gates are governance, so they get tests like any rule.
Runs in dry-run against the fixture index; no model calls, no network."""
import os
import time

import pytest


@pytest.fixture(scope="module")
def client(indexed):
    os.environ["TRAILHEADRX_ACCESS_CODE"] = "open-sesame"
    os.environ["TRAILHEADRX_SECRET"] = "test-secret"
    from fastapi.testclient import TestClient
    from trailheadrx.web import app as webapp
    return TestClient(webapp.app)


def _sign_in(client):
    r = client.post("/enter", data={"code": "open-sesame"}, follow_redirects=False)
    assert r.status_code == 303 and "trx_access" in r.cookies
    return r.cookies["trx_access"]


def _wait(client, url, seconds=60):
    for _ in range(seconds * 4):
        r = client.get(url)
        if "refresh" not in r.text:
            return r
        time.sleep(0.25)
    raise AssertionError("job did not finish")


def test_access_code_required(client):
    assert "Access code" in client.get("/").text
    r = client.post("/enter", data={"code": "wrong"})
    assert r.status_code == 401 and "did not match" in r.text
    assert "wrong" not in r.text.split("did not match")[0][-200:]   # never echoed
    _sign_in(client)
    assert "What does your plan require?" in client.get("/").text


def test_form_lists_only_known_payers_and_drugs(client):
    _sign_in(client)
    html = client.get("/").text
    assert "UnitedHealthcare" in html and "Emgality" in html
    assert "Reyvow" not in html                       # discontinued
    assert "Ohio Revised Code" not in html            # references are not plans
    assert "FIXTURE State Code" not in html
    assert "What do you want to know?" in html


def test_phi_refused_before_queueing(client):
    _sign_in(client)
    r = client.post("/ask", data={"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                  "preset": "other", "question": "my member id is AB12345678, what do I need?"})
    assert r.status_code == 200 and "take that question as written" in r.text
    assert "AB12345678" not in r.text


def test_answer_page_end_to_end(client):
    _sign_in(client)
    r = client.post("/ask", data={"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                  "question": "What do I have to try before Emgality is covered?"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/a/")
    page = _wait(client, r.headers["location"])
    html = page.text
    assert "The short version" in html and "Where this comes from" in html
    assert "How this answer was built" in html and "Passages retrieved" in html
    assert "Other ways to get it" in html and "LillyDirect" in html


def test_spend_cap_blocks_when_over(client, monkeypatch):
    _sign_in(client)
    from trailheadrx.web import app as webapp, gate
    monkeypatch.setattr(webapp.gate, "spend_today", lambda *_: gate.Spend(9.0, 5.0))
    monkeypatch.setattr(webapp.config, "dry_run", lambda: False)      # cap only bites live
    r = client.post("/ask", data={"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                  "question": "What is required?"})
    assert r.status_code == 503 and "budget for today" in r.text


def test_rate_limit(client):
    _sign_in(client)
    from trailheadrx.web import app as webapp
    webapp.limiter.per_hour = 2
    webapp.limiter._hits.clear()
    for _ in range(2):
        r = client.post("/ask", data={"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                      "question": "What is required?"}, follow_redirects=False)
        assert r.status_code == 303
    r = client.post("/ask", data={"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                  "question": "What is required?"})
    assert r.status_code == 429
    webapp.limiter.per_hour = 100


def test_preset_or_own_words_required(client):
    _sign_in(client)
    r = client.post("/ask", data={"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                  "preset": "other", "question": "   "})
    assert r.status_code == 400 and "Pick a question" in r.text
