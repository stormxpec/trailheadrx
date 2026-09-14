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

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config
from ..guardrails import input_guardrails
from ..pipeline import SECTION_HEADINGS
from ..retrieve import load_drugs, open_db
from ..rules import eligibility_summary
from . import gate
from .jobs import JobRunner

app = FastAPI(title="Trailhead Rx", docs_url=None, redoc_url=None)
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

def _ip(request: Request) -> str:
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
    ctx.update({"request": request, "dry_run": config.dry_run()})
    return templates.TemplateResponse(request, name, ctx, status_code=status)


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
    return _page(request, "ask.html", payers=_payers(), drugs=_drugs(), lobs=LOBS, presets=PRESETS, spend=spend)


@app.post("/enter")
def enter(request: Request, code: str = Form("")):
    ip = _ip(request)
    if limiter.login_locked(ip):
        return _page(request, "enter.html", 429, error="Too many tries. Please wait an hour.")
    if not gate.code_ok(code):
        limiter.bad_login(ip)               # counted, never logged with the text typed
        return _page(request, "enter.html", 401, error="That code did not match.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, gate.cookie_token(), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/leave")
def leave():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.post("/ask")
def ask(request: Request, payer: str = Form(...), lob: str = Form(...), drug: str = Form(...),
        preset: str = Form("try_first"), question: str = Form(""), self_funded: str = Form("unknown"),
        compare: str = Form("")):
    if not _signed_in(request):
        return RedirectResponse("/", status_code=303)
    ctx = dict(payers=_payers(), drugs=_drugs(), lobs=LOBS, presets=PRESETS)

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
    if lob not in {k for k, _ in LOBS}:
        return _page(request, "ask.html", 400, error="Please pick a plan type.", **ctx, spend=spend)
    g = input_guardrails(drug, question)
    if not g.passed:
        return _page(request, "refused.html", 200, reason=g.reason,
                     eligibility=[e.__dict__ for e in eligibility_summary(lob)])

    sf = {"yes": True, "no": False}.get(self_funded)
    job = runner.submit({"question": question.strip(), "payer": payer, "lob": lob, "drug": drug,
                         "self_funded": sf, "compare": bool(compare)})
    return RedirectResponse(f"/a/{job.id}", status_code=303)


@app.get("/a/{job_id}", response_class=HTMLResponse)
def show(request: Request, job_id: str):
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
                 table=job.table, trace=job.trace or {},
                 chunks=(job.trace or {}).get("stages", []) and _packet_from_trace(job.trace))


def _packet_from_trace(trace: dict) -> list[dict]:
    for st in trace.get("stages", []):
        if st.get("stage") == "retrieval" and st.get("packet"):
            return st["packet"]
    return []
