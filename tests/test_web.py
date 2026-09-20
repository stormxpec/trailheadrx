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
    assert "Your health plan" in client.get("/").text


def test_form_lists_only_known_payers_and_drugs(client):
    _sign_in(client)
    html = client.get("/").text
    assert "UnitedHealthcare" in html and "Emgality" in html
    assert "Reyvow" not in html                       # discontinued
    assert "Ohio Revised Code" not in html            # references are not plans
    assert "FIXTURE State Code" not in html
    assert "What do you want to know?" in html
    assert "UnitedHealthcare — employer or individual plan" in html
    assert "UMR — self-funded employer plan (UnitedHealthcare)" in html   # alias of a held plan
    assert "Meritain Health — self-funded" not in html                     # Aetna not held in the fixture index


def test_phi_refused_before_queueing(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality",
                                  "preset": "other", "question": "my member id is AB12345678, what do I need?"})
    assert r.status_code == 200 and "take that question as written" in r.text
    assert "AB12345678" not in r.text


def test_answer_page_end_to_end(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality",
                                  "question": "What do I have to try before Emgality is covered?"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/a/")
    page = _wait(client, r.headers["location"])
    html = page.text
    assert "The short version" in html and "Where this comes from" in html
    assert "How this answer was built" in html and "Passages retrieved" in html
    assert "Your path options to" in html and "LillyDirect" in html and "Your plan's path" in html and "Skip the path altogether" in html


def test_spend_cap_blocks_when_over(client, monkeypatch):
    _sign_in(client)
    from trailheadrx.web import app as webapp, gate
    monkeypatch.setattr(webapp.gate, "spend_today", lambda *_: gate.Spend(9.0, 5.0))
    monkeypatch.setattr(webapp.config, "dry_run", lambda: False)      # cap only bites live
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality",
                                  "question": "What is required?"})
    assert r.status_code == 503 and "budget for today" in r.text


def test_rate_limit(client):
    _sign_in(client)
    from trailheadrx.web import app as webapp
    webapp.limiter.per_hour = 2
    webapp.limiter._hits.clear()
    for _ in range(2):
        r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality",
                                      "question": "What is required?"}, follow_redirects=False)
        assert r.status_code == 303
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality",
                                  "question": "What is required?"})
    assert r.status_code == 429
    webapp.limiter.per_hour = 100


def test_preset_or_own_words_required(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality",
                                  "preset": "other", "question": "   "})
    assert r.status_code == 400 and "Pick a question" in r.text


def test_plan_not_listed_records_name_only(client, tmp_path, monkeypatch):
    _sign_in(client)
    monkeypatch.setenv("TRAILHEADRX_REQUESTED_PLANS", str(tmp_path / "req.jsonl"))
    r = client.post("/ask", data={"plan": "other", "plan_other": "Paramount Advantage", "drug": "Emgality",
                                  "preset": "try_first"})
    assert r.status_code == 200 and "Paramount Advantage" in r.text and "check back" in r.text
    line = (tmp_path / "req.jsonl").read_text().strip()
    assert '"plan": "Paramount Advantage"' in line and "Emgality" not in line


def test_plan_alias_resolves_and_sets_self_funded(client):
    _sign_in(client)
    from trailheadrx.web import plans
    c = plans.match_alias("my card says UMR on it")
    assert c and c.payer == "UnitedHealthcare" and c.lob == "commercial" and c.self_funded is True
    r = client.post("/ask", data={"plan": "other", "plan_other": "UMR", "drug": "Emgality", "preset": "try_first"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/a/")


def test_plan_other_is_phi_checked(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "other", "plan_other": "UMR member id: AB12345678", "drug": "Emgality",
                                  "preset": "try_first"})
    assert r.status_code == 200 and "take that question as written" in r.text


def test_compare_button_adds_comparison(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality", "preset": "try_first"},
                    follow_redirects=False)
    page = _wait(client, r.headers["location"])
    assert "Compare comparable medications" in page.text
    job_id = r.headers["location"].rsplit("/", 1)[1]
    r2 = client.post(f"/a/{job_id}/compare", data={"go": "1"}, follow_redirects=False)
    assert r2.status_code == 303 and r2.headers["location"] != r.headers["location"]
    page2 = _wait(client, r2.headers["location"], seconds=120)
    assert "Your path options with the alternatives" in page2.text
    assert 'class="mx"' in page2.text and "Skip the path altogether" in page2.text      # matrix, with row C (LillyDirect)
    # every path is drawn; the ones a plan type rules out are greyed with the reason (Lilly Cares on commercial)
    assert "Free medicine if you qualify" in page2.text and "Not for your kind of plan" in page2.text
    assert "commercially insured members are not eligible" in page2.text
    assert "only if your plan will not cover it" in page2.text



def test_expand_all_opens_every_section(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality", "preset": "try_first"},
                    follow_redirects=False)
    page = _wait(client, r.headers["location"])
    closed = page.text.count('<details class="sec">')
    page2 = client.get(r.headers["location"] + "?expand=1")
    assert closed > 0 and '<details class="sec">' not in page2.text
    assert page2.text.count('<details class="sec" open>') == closed


def test_other_plans_button(client):
    _sign_in(client)
    r = client.post("/ask", data={"plan": "UnitedHealthcare|commercial|", "drug": "Emgality", "preset": "try_first"},
                    follow_redirects=False)
    page = _wait(client, r.headers["location"])
    assert "What if I had a different plan?" in page.text and 'action="/a/' in page.text
    job_id = r.headers["location"].rsplit("/", 1)[1]
    r2 = client.post(f"/a/{job_id}/plans", data={"go": "1"}, follow_redirects=False)
    page2 = _wait(client, r2.headers["location"], seconds=120)
    # the fixture holds one commercial payer, so the section explains there is nothing to compare yet
    assert "nothing to compare" in page2.text
    assert "Find out what your employer's plan does" in page2.text
    assert 'action="/a/' + r2.headers["location"].rsplit("/", 1)[1] + '/plans"' not in page2.text   # button gone once loaded


def test_failed_job_can_be_retried(client):
    from trailheadrx.web import app as webapp
    _sign_in(client)
    job = webapp.runner.submit({"payer": "UnitedHealthcare", "lob": "commercial", "drug": "Emgality",
                                "question": "What must be tried first?", "self_funded": None, "compare": False, "free_text": False})
    import time
    for _ in range(60):
        if job.status in ("done", "failed"):
            break
        time.sleep(1)
    job.status, job.error = "failed", "APITimeoutError: Request timed out"
    page = client.get(f"/a/{job.id}")
    assert "connection to the model dropped" in page.text and f'action="/a/{job.id}/retry"' in page.text
    r = client.post(f"/a/{job.id}/retry", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] != f"/a/{job.id}"
