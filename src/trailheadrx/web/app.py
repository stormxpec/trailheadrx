"""The website: a form in front of the same pipeline the CLI runs.

Routes
  GET  /            the access-code page, or the question form once signed in
  POST /enter       check the access code; set a signed cookie
  POST /ask         run the gates (cookie, rate limit, spend cap, input
                    guardrails), then queue the answer and redirect to it
  GET  /a/{job_id}  "working…" page that refreshes itself, then the answer
  GET  /health      for the hosting platform's health check
  GET  /leave       clear the cookie

Design choices, in plain words:
  - No JavaScript. The waiting page uses a meta-refresh tag, so every file
    here is readable and there is nothing to build.
  - The form only offers payers and medicines we have records for, so the
    most common way to get an "I don't have that document" answer is closed
    off before the question is asked.
  - Everything the CLI prints is on the page in the same order, plus a
    collapsed "How this answer was built" panel that shows the plan match,
    the passages retrieved, the verifier's per-claim verdicts, and cost.

Run locally:  uvicorn trailheadrx.web.app:app --reload   (PYTHONPATH=src)
FLUENCY.md: "guardrails", "chain", "tracing".
"""
from __future__ import annotations

from pathlib import Path

import os

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config
from ..guardrails import input_guardrails
from ..pipeline import SECTION_HEADINGS
from ..retrieve import load_drugs, open_db
from ..rules import eligibility_summary
from . import gate, plans
from .jobs import JobRunner

app = FastAPI(title="Trailhead Rx", docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Standard browser protections. No inline scripts exist (the site has no
    JavaScript), so the content-security policy can be strict: same-origin
    everything, no frames, no plugins."""
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if HTTPS:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
limiter = gate.Limiter()
runner = JobRunner()

COOKIE = "trx_access"
LOBS = [
    ("commercial", "Through an employer, or a plan I bought myself"),
    ("marketplace", "Healthcare.gov / marketplace plan"),
    ("medicare_advantage", "Medicare Advantage"),
    ("medicaid_mco", "Ohio Medicaid (CareSource, Molina, Buckeye, Anthem, Humana, AmeriHealth)"),
]


# ---- helpers ---------------------------------------------------------------

HTTPS = os.environ.get("TRAILHEADRX_HTTPS") == "1"   # set in the Fly image; off on a laptop


def _ip(request: Request) -> str:
    """The visitor's address for rate limiting. Behind Fly's proxy the real
    address arrives in Fly-Client-IP (one value, set by Fly, not spoofable);
    X-Forwarded-For is the general convention; the socket address is the
    laptop case."""
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))


def _signed_in(request: Request) -> bool:
    return gate.cookie_ok(request.cookies.get(COOKIE))


PRESETS = [
    ("try_first", "What do I have to try before my plan will cover this medicine?"),
    ("doctor", "What does my doctor need to show for my plan to approve it?"),
    ("how_long", "How long will approval take, and is there a faster way?"),
    ("appeal", "My plan denied it. How do I appeal, and how long do they have to answer?"),
    ("cost", "What will it cost me, and are there other ways to get it, including paying cash?"),
    ("other", "Something else — I'll write it below"),
]


def _payers() -> list[tuple[str, str]]:
    """(value, label) pairs for the health-plan menu: payers with at least one
    indexed document, so the form only offers plans we can actually read.
    The label says which kinds of plan we hold for that payer, so a visitor
    is not invited into a combination we cannot answer. Law references are
    never plans."""
    conn = open_db()
    rows = conn.execute("SELECT DISTINCT payer, line_of_business FROM documents").fetchall()
    conn.close()
    kinds: dict[str, set[str]] = {}
    for r in rows:
        lob = str(r["line_of_business"])
        if lob.startswith("reference"):
            continue
        name = r["payer"].split(" / ")[0].split(" — ")[0].strip()
        kinds.setdefault(name, set()).add(lob)
    # Ohio Medicaid MCOs are governed by the state list; offer them by name.
    if any("medicaid_ffs" in k for k in kinds.values()):
        for mco in ("CareSource", "Molina", "Buckeye", "AmeriHealth Caritas", "Humana Healthy Horizons", "Anthem"):
            kinds.setdefault(mco, set()).add("medicaid_mco")
    pretty = {"commercial": "employer / individual", "marketplace": "marketplace", "medicare_advantage": "Medicare Advantage",
              "medicaid_mco": "Ohio Medicaid", "medicaid_ffs": "Ohio Medicaid"}
    out = []
    for name in sorted(kinds):
        labels = sorted({pretty.get(k, k) for k in kinds[name]})
        out.append((name, f"{name} — {', '.join(labels)}"))
    return out


def _drugs() -> list[dict]:
    d = load_drugs()
    out = []
    for group, label in (("preventive_branded", "Prevention"), ("acute_branded", "Treating an attack")):
        for rec in d.get(group, []):
            if rec.get("status") == "discontinued":
                continue
            out.append({"brand": rec["brand"], "generic": rec.get("generic", ""), "group": label})
    return out


def _page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    ctx.update({"request": request, "dry_run": config.dry_run(), "docs_checked": _docs_checked()})
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def _docs_checked() -> str:
    """'Plan documents last checked <date>' for the footer: from the refresh
    job's state, or the newest download date in the manifest before the job
    has ever run."""
    try:
        from ..refresh import last_checked
        d = last_checked()
        if d:
            return d
    except Exception:
        pass
    try:
        from ..retrieve import load_manifest
        return max(str(x.get("downloaded_on") or "") for x in load_manifest() if x.get("status") == "found")
    except Exception:
        return ""


def _sections(answer) -> list[tuple[str, list[dict]]]:
    out = []
    for title, topics in SECTION_HEADINGS:
        group = [c for c in answer.claims if c.get("topic", "other") in topics]
        if group:
            out.append((title, group))
    return out


def _wait_text(answer) -> str:
    w = answer.wait_estimate or {}
    if not w.get("months_high"):
        return ""
    lo, hi = w.get("months_low"), w.get("months_high")
    rng = f"about {hi} months" if not lo or lo == hi else f"about {lo} to {hi} months"
    return (f"If you are starting from zero, the path your plan describes could take {rng} before "
            f"{answer.drug or 'the medicine'} is approved ({(w.get('explanation') or '').rstrip('.')}). It can be "
            f"shorter if you have already tried some of these medicines, or if you qualify for an exception.")


# ---- routes ----------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"ok": True, "dry_run": config.dry_run(), "in_flight": runner.in_flight()}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not gate.access_code():
        return _page(request, "enter.html", 503, error="The site has no access code configured. Set TRAILHEADRX_ACCESS_CODE.")
    if not _signed_in(request):
        return _page(request, "enter.html")
    spend = gate.spend_today(runner.in_flight_reserve_usd())
    return _page(request, "ask.html", plans=plans.choices(), drugs=_drugs(), presets=PRESETS, spend=spend)


@app.post("/enter")
def enter(request: Request, code: str = Form("")):
    ip = _ip(request)
    if limiter.login_locked(ip):
        return _page(request, "enter.html", 429, error="Too many tries. Please wait an hour.")
    if not gate.code_ok(code):
        limiter.bad_login(ip)               # counted, never logged with the text typed
        return _page(request, "enter.html", 401, error="That code did not match.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, gate.cookie_token(), httponly=True, samesite="lax", secure=HTTPS, max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/leave")
def leave():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/ask")
def ask(request: Request, plan: str = Form(...), plan_other: str = Form(""), drug: str = Form(...),
        preset: str = Form("try_first"), question: str = Form(""), self_funded: str = Form("unknown"),
        compare: str = Form("")):
    if not _signed_in(request):
        return RedirectResponse("/", status_code=303)
    ctx = dict(plans=plans.choices(), drugs=_drugs(), presets=PRESETS)

    # The question is a chosen one, the visitor's own words, or both.
    chosen = dict(PRESETS).get(preset, "") if preset != "other" else ""
    own = question.strip()
    if chosen and own:
        question = f"{chosen} In particular: {own}"
    else:
        question = chosen or own
    if not question:
        return _page(request, "ask.html", 400, error="Pick a question, or write one in your own words.", **ctx, spend=gate.spend_today())

    # Gate 1: rate limit (per visitor).
    allowed, remaining = limiter.allow(_ip(request))
    if not allowed:
        return _page(request, "ask.html", 429, error="You have asked the most questions this hour allows. "
                     "Please come back in a little while.", **ctx, spend=gate.spend_today())

    # Gate 2: spend cap (whole site, per day).
    spend = gate.spend_today(runner.in_flight_reserve_usd())
    if spend.over and not config.dry_run():
        return _page(request, "ask.html", 503, error="Trailhead Rx has reached its budget for today, so it is not "
                     "reading new questions. Please try again tomorrow.", **ctx, spend=spend)

    # Gate 3: input guardrails, before anything is queued. The pipeline runs
    # them again; this just refuses fast without spending a worker.
    g = input_guardrails(drug, question if plan != "other" else f"{question} {plan_other}")
    if not g.passed:
        return _page(request, "refused.html", 200, reason=g.reason, eligibility=[])

    # The plan: a menu choice, or a typed name matched against the alias table.
    if plan == "other":
        choice = plans.match_alias(plan_other)
        if choice is None:
            plans.record_request(plan_other)
            return _page(request, "notlisted.html", 200, plan_name=plan_other.strip()[:80])
    else:
        choice = plans.parse(plan)
        if choice is None:
            return _page(request, "ask.html", 400, error="Please pick your health plan.", **ctx, spend=spend)

    sf = {"yes": True, "no": False}.get(self_funded)
    if choice.self_funded is True:
        sf = True                        # the card name settles it (Meritain, UMR)
    job = runner.submit({"question": question.strip(), "payer": choice.payer, "lob": choice.lob, "drug": drug,
                         "self_funded": sf, "compare": bool(compare), "free_text": bool(own),
                         "preset": "other" if own else preset})
    return RedirectResponse(f"/a/{job.id}", status_code=303)


@app.post("/a/{job_id}/compare")
def add_compare(request: Request, job_id: str):
    """The 'Compare comparable medications' button on an answer page: re-run
    the same request with the comparison on. The main answer comes back from
    the cache when there is no free text, so only the comparison costs time."""
    if not _signed_in(request):
        return RedirectResponse("/", status_code=303)
    job = runner.get(job_id)
    if job is None or job.status != "done":
        return RedirectResponse(f"/a/{job_id}", status_code=303)
    allowed, _ = limiter.allow(_ip(request))
    if not allowed:
        return RedirectResponse(f"/a/{job_id}", status_code=303)
    new = runner.submit(dict(job.request, compare=True))
    return RedirectResponse(f"/a/{new.id}", status_code=303)


@app.post("/a/{job_id}/retry")
def retry(request: Request, job_id: str):
    """Re-run a failed job with the same request, so a network fault does not
    send the visitor back to the form."""
    if not _signed_in(request):
        return RedirectResponse("/", status_code=303)
    job = runner.get(job_id)
    if job is None or job.status != "failed":
        return RedirectResponse(f"/a/{job_id}" if job else "/", status_code=303)
    allowed, _ = limiter.allow(_ip(request))
    if not allowed:
        return RedirectResponse(f"/a/{job_id}", status_code=303)
    new = runner.submit(dict(job.request))
    return RedirectResponse(f"/a/{new.id}", status_code=303)


@app.post("/a/{job_id}/plans")
def add_plans(request: Request, job_id: str):
    """'What if I had a different plan?' — re-run the same request with the
    cross-plan comparison on. Same pattern as /compare; the main answer
    comes back from the cache."""
    if not _signed_in(request):
        return RedirectResponse("/", status_code=303)
    job = runner.get(job_id)
    if job is None or job.status != "done":
        return RedirectResponse(f"/a/{job_id}", status_code=303)
    allowed, _ = limiter.allow(_ip(request))
    if not allowed:
        return RedirectResponse(f"/a/{job_id}", status_code=303)
    new = runner.submit(dict(job.request, plans=True))
    return RedirectResponse(f"/a/{new.id}", status_code=303)


@app.get("/a/{job_id}", response_class=HTMLResponse)
def show(request: Request, job_id: str, expand: str = ""):
    if not _signed_in(request):
        return RedirectResponse("/", status_code=303)
    job = runner.get(job_id)
    if job is None:
        return _page(request, "waiting.html", 404, job=None)
    if job.status in ("queued", "running"):
        return _page(request, "waiting.html", job=job, elapsed=int(__import__("time").time() - job.created))
    if job.status == "failed":
        return _page(request, "waiting.html", 500, job=job)
    a = job.answer
    return _page(request, "result.html", job=job, a=a, sections=_sections(a), wait_text=_wait_text(a),
                 expand=(expand == "1"), compare_paths=_compare_paths(job), wait=_wait(job),
                 rows=_path_rows(job), plan_rows=_plan_rows(job), focus=_focus(job), peer_months=_peer_months(job),
                 table=job.table, trace=job.trace or {},
                 chunks=(job.trace or {}).get("stages", []) and _packet_from_trace(job.trace))


def _wait(job) -> dict | None:
    """The lead-time estimate. When the class comparison ran, use its row for
    this medicine: the months there are computed in code from the trial
    lengths the extractor pulled out (compare.months_from_steps), the same
    rule for every medicine. Otherwise use the model's own estimate from the
    answer, which is built the same way but by the model."""
    if job.table and job.table[1]:
        mine = job.request["drug"].split()[0].lower()
        for r in job.table[1]:
            if r.status == "ok" and r.months_high and r.drug.split()[0].lower() == mine:
                tries = "; ".join(f"{x.get('what')} ({x.get('days')} days)" for x in r.steps if x.get("days"))
                return {"months_low": r.months_low, "months_high": r.months_high,
                        "explanation": f"{tries}, plus about two weeks for the plan's review"}
    w = job.answer.wait_estimate
    if w and w.get("months_high"):
        return w
    return None


# Which path the question points at. The preset is a menu choice, so this is a
# lookup, not a judgment: the model never decides what to emphasize.
FOCUS = {
    "try_first": {"paths": ["A"], "open": ["What you need to try first"],
                  "text": "Your question was about what to try first. That is path A, the plan's standard route; the lead time above is how long it takes from scratch."},
    "doctor":    {"paths": ["A"], "open": ["What your doctor needs to show"],
                  "text": "Your question was about what your doctor needs to show. That is path A, the plan's standard route; the specifics are under \"What your doctor needs to show\" below."},
    "how_long":  {"paths": ["B"], "open": ["Timing and appeals"],
                  "text": "Your question was about timing. Path A is the plan's standard route and how long it takes from scratch. Path B is a possible way to shorten it, if you qualify."},
    "appeal":    {"paths": ["B", "E"], "open": ["Timing and appeals"],
                  "text": "Your question was about a denial. From where you are, path B (asking for an exception) is the quickest route, and path E is the appeal; the plan's deadlines are under \"Timing and appeals\" below."},
    "cost":      {"paths": ["C", "D"], "open": [],
                  "text": "Your question was about cost. The right-hand column is what you pay on each path; paths C and D are the routes that do not go through the plan at all."},
}


def _focus(job) -> dict:
    f = FOCUS.get(job.request.get("preset") or "")
    return dict(f) if f else {"paths": [], "open": [], "text": ""}


def _plan_rows(job) -> list[dict] | None:
    """Cells for 'What if I had a different plan?': this plan first, then
    each other held plan of the same kind. None when not requested; an empty
    list when we hold no other plan of this kind."""
    if job.plans is None:
        return None
    from ..compare import months_from_steps
    out = []
    w = _wait(job) or {}
    me = None
    if job.table and job.table[1]:
        mine = job.request["drug"].split()[0].lower()
        me = next((r for r in job.table[1] if r.status == "ok" and r.drug.split()[0].lower() == mine), None)
    out.append({"payer": job.request["payer"], "mine": True, "ok": True,
                "months": _months_txt(w.get("months_low"), w.get("months_high")),
                "approval": me.approval_required if me else None,
                "short": (me.short if me else (job.answer.summary_points[0] if job.answer.summary_points else "")),
                "try_first": me.must_try_first if me else "", "trial": me.trial_length if me else "",
                "steps": me.steps if me else [], "notes": me.notes if me else "", "reason": ""})
    for r in job.plans:
        ok = r.status == "ok"
        out.append({"payer": r.payer, "mine": False, "ok": ok, "months": _months_txt(r.months_low, r.months_high) if ok else "",
                    "approval": r.approval_required if ok else None, "short": (r.short or r.must_try_first[:40]) if ok else "",
                    "try_first": r.must_try_first if ok else "", "trial": r.trial_length if ok else "",
                    "steps": r.steps if ok else [], "notes": r.notes if ok else "", "reason": "" if ok else r.reason})
    return out


def _months_txt(lo, hi) -> str:
    if not hi:
        return ""
    return f"{lo}–{hi}" if lo and lo != hi else str(hi)


def _path_rows(job) -> dict:
    """All four paths are always drawn, so the reader sees what is NOT open
    to them and why (Ben, fix33: "show those which are not available based
    on the plan"). A row that applies to nobody on the page is kept, greyed,
    with the reason in the cell; the notes below the grid say it once in a
    sentence. Reasons come from rules-as-code (copay_card_allowed,
    assistance_allowed), never from the model."""
    R_all = [{r["key"]: r for r in job.answer.routes}] if job.answer.routes else []
    R_all += [c["R"] for c in _compare_paths(job)]
    any_c = any(R.get("dtc") for R in R_all)
    any_d = any(R.get("pap") and R["pap"]["available"] for R in R_all)
    notes = []
    if not any_d and any(R.get("pap") for R in R_all):
        lob = job.request["lob"]
        if lob in ("medicaid_mco", "medicaid_ffs"):
            notes.append("Makers' free-medicine programs do not take Medicaid members; Medicaid covers the medicine at little or no cost.")
        else:
            notes.append("For these medicines, the makers' free-medicine programs take people with no insurance or on Medicare, not commercially insured members — shown greyed so you know they exist and why they do not apply to you.")
    if not any_c:
        notes.append("None of these makers sells the medicine directly for cash, so there is no \"skip the path\" option for this class.")
    return {"c": True, "d": True, "notes": notes}


def _compare_paths(job) -> list[dict]:
    """One entry per medicine in the class comparison, with everything the
    page needs to draw the same four-path grid it draws for the asked-about
    medicine: the plan's first requirement, months from scratch, and the
    maker's routes (bridge/card/dtc/pap) keyed by name."""
    if not job.table or not job.table[1]:
        return []
    from ..programs import routes_structured
    lob = job.request["lob"]
    mine = job.request["drug"].split()[0].lower()
    out = []
    for r in job.table[1]:
        R = {x["key"]: x for x in routes_structured(r.drug, lob)}
        ok = r.status == "ok"
        is_mine = r.drug.split()[0].lower() == mine
        months = _months_txt(r.months_low, r.months_high) if ok else ""
        note = "" if ok else _cell_reason(r)
        short = (r.short or r.must_try_first[:40]) if ok else ""
        approval = r.approval_required if ok else None
        if is_mine and not ok:
            # The asked-about medicine's column must never contradict the
            # verified answer at the top of the page: the comparison's own
            # re-reading of the same document can fail its second check
            # (shared documents, neighbouring drugs) while the full answer
            # passed verification. Fall back to that answer.
            w = job.answer.wait_estimate or {}
            months = _months_txt(w.get("months_low"), w.get("months_high"))
            short = (job.answer.summary_points[0] if job.answer.summary_points else "")[:60]
            approval = True if job.answer.summary_points else None
            note = ""
        out.append({"drug": r.drug, "mine": is_mine, "months": months, "note": note, "R": R,
                    "try_first": r.must_try_first if ok else "", "trial": r.trial_length if ok else "",
                    "short": short, "notes": r.notes if ok else "",
                    "steps": r.steps if ok else [], "trial_days": r.trial_days if ok else 0,
                    "approval": approval})
    out.sort(key=lambda c: not c["mine"])   # the asked-about medicine first, so it is the first column on a phone
    return out


def _cell_reason(r) -> str:
    """Plain words for a comparison cell we could not fill, by what actually
    happened — never a generic 'not loaded' when the document is loaded."""
    if r.status == "not_in_corpus":
        return r.reason.split(". ")[0].rstrip(".") + "."      # the first sentence says why; the rest is for the full page
    if r.status == "no_passages":
        return "The plan documents we hold do not mention this medicine."
    return ("We could not read this plan's rule for this medicine reliably, so this cell is left blank "
            "rather than guessed. The plan's own document is in the sources below.")


def _peer_months(job) -> str:
    """When a lead time cannot be computed, the honest comparison is the other
    columns on the same page: the range of months across the other medicines
    (class comparison) and other plans (plan comparison) that did compute.
    Empty when nothing on the page has a number."""
    los, his = [], []
    cells = [c for c in _compare_paths(job) if not c["mine"]]
    cells += [c for c in (_plan_rows(job) or []) if not c["mine"] and c["ok"]]
    for c in cells:
        m = c.get("months") or ""
        parts = m.replace("–", "-").split("-")
        try:
            lo, hi = int(parts[0]), int(parts[-1])
        except (ValueError, IndexError):
            continue
        los.append(lo); his.append(hi)
    if not his:
        return ""
    return _months_txt(min(los), max(his))


@app.on_event("startup")
def _warm_up() -> None:
    """Load the embedding model once at start so the first visitor does not
    pay the 10–20 s it takes; the index is built at deploy, not on request."""
    try:
        from ..ingest import embed
        embed(["warm up"])
    except Exception:
        pass   # dry-run or missing model: the first request will load it


def _packet_from_trace(trace: dict) -> list[dict]:
    for st in trace.get("stages", []):
        if st.get("stage") == "retrieval" and st.get("packet"):
            return st["packet"]
    return []
