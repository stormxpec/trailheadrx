"""Background answers for the website.

A live answer takes one to two minutes (draft, judge, sometimes a retry), too
long for a browser to sit on one request. So the form submits, a job is
queued, and the page refreshes itself every few seconds until the job is
done. Jobs live in memory only: a small pool of worker threads, a dict of
results, and a sweep that drops finished jobs after `job_ttl_minutes`.
Nothing here is written to disk — the audit trace (which never holds a
refused question) is the only record, and it is written by the pipeline as
it always was.

FLUENCY.md: "chain" (the same one the CLI runs), "tracing".
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .. import config
from ..audit import read_all

IN_FLIGHT_RESERVE_USD = 0.30   # assumed cost of a job that has not finished yet (answer + comparisons)


@dataclass
class Job:
    id: str
    request: dict
    status: str = "queued"          # queued | running | done | failed
    created: float = field(default_factory=time.time)
    answer: object = None           # pipeline.Answer
    table: tuple | None = None      # (class name, rows) when compare was requested
    plans: list | None = None       # rows, one per other held plan, when plans was requested
    trace: dict | None = None       # the audit record for this answer
    error: str = ""


class JobRunner:
    def __init__(self, workers: int | None = None):
        w = config.CONFIG.get("web", {})
        self.ttl = 60 * int(w.get("job_ttl_minutes", 60))
        self._pool = ThreadPoolExecutor(max_workers=workers or int(w.get("job_workers", 2)))
        self._side = ThreadPoolExecutor(max_workers=2)   # class comparisons, run beside the main answer
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------------

    def submit(self, request: dict) -> Job:
        job = Job(uuid.uuid4().hex[:10], request)
        with self._lock:
            self._sweep()
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def in_flight(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in ("queued", "running"))

    def in_flight_reserve_usd(self) -> float:
        return self.in_flight() * IN_FLIGHT_RESERVE_USD

    # -- internals ------------------------------------------------------------

    def _sweep(self) -> None:
        cutoff = time.time() - self.ttl
        for jid in [j for j, job in self._jobs.items() if job.status in ("done", "failed") and job.created < cutoff]:
            del self._jobs[jid]

    def _run(self, job: Job) -> None:
        job.status = "running"
        try:
            job.answer, job.table, job.plans, job.trace = compute(job.request, side=self._side)
            job.status = "done"
        except Exception as e:   # the page must never hang on an exception in a worker
            job.error = f"{type(e).__name__}: {e}"
            job.status = "failed"


def compute(r: dict, side: ThreadPoolExecutor | None = None, use_cache: bool = True):
    """One request → (answer, class table, other-plans rows, trace). Shared by
    the web worker and the nightly precompute, so both produce the same thing
    and both fill the same cache."""
    from ..pipeline import answer
    if use_cache:
        cached = _cache_get(r)
        if cached:
            return cached
    side = side or ThreadPoolExecutor(max_workers=2)
    # The comparisons do not depend on the main answer, so they run beside it.
    table_future = plans_future = None
    if r.get("compare"):
        from ..compare import compare_class
        table_future = side.submit(compare_class, r["drug"], r["payer"], r["lob"])
    if r.get("plans"):
        from ..compare import compare_plans
        plans_future = side.submit(compare_plans, r["drug"], r["payer"], r["lob"])
    ans = _with_one_retry(lambda: answer(r["question"], r["payer"], r["lob"], r["drug"],
                                         self_funded=r.get("self_funded"), use_judge=True))
    table = plans = None
    if table_future is not None:
        name, rows, _calls = table_future.result()
        if ans.outcome == "answered":
            table = (name, rows)
    if plans_future is not None:
        rows, _calls = plans_future.result()
        if ans.outcome == "answered":
            plans = rows
    trace = _find_trace(ans.trace_id)
    result = (ans, table, plans, trace)
    if ans.outcome == "answered":
        _cache_put(r, result)
    return result


def _with_one_retry(fn):
    """A timed-out or dropped model connection is retried inside the SDK; if
    it still fails, try the whole answer once more before giving up, since a
    transient network fault should not cost the visitor their answer."""
    try:
        return fn()
    except Exception as e:
        if type(e).__name__ in ("APITimeoutError", "APIConnectionError"):
            time.sleep(3)
            return fn()
        raise


# ---- answer cache ------------------------------------------------------------
# The menu of questions is finite (plan × medicine × preset question), so a
# repeat of the same request can be served from memory instead of re-running
# a minute of model calls. Only requests with NO free text are cached — free
# text is the one thing a visitor writes, and it is never stored. Entries
# expire after `cache_hours` (config) so a corpus refresh shows up.

_CACHE: dict[tuple, tuple[float, tuple]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_dir():
    """Disk copy of the cache, so a restart or redeploy keeps last night's
    precomputed answers. TRAILHEADRX_CACHE_DIR (the /data volume on the
    server); unset on a laptop means memory only."""
    import os
    from pathlib import Path
    d = os.environ.get("TRAILHEADRX_CACHE_DIR")
    return Path(d) if d else None


def _cache_file(k: tuple):
    import hashlib
    d = _cache_dir()
    if d is None:
        return None
    return d / (hashlib.sha256(repr(k).encode()).hexdigest()[:24] + ".pkl")


def _cache_key(r: dict) -> tuple | None:
    if r.get("free_text"):
        return None
    return (r["payer"], r["lob"], r["drug"], r.get("self_funded"), r["question"], bool(r.get("compare")), bool(r.get("plans")))


def _cache_get(r: dict):
    k = _cache_key(r)
    if k is None:
        return None
    ttl = 3600 * float(config.CONFIG.get("web", {}).get("cache_hours", 24))
    with _CACHE_LOCK:
        hit = _CACHE.get(k)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        _CACHE.pop(k, None)
    f = _cache_file(k)
    if f is not None and f.exists() and time.time() - f.stat().st_mtime < ttl:
        import pickle
        try:
            with open(f, "rb") as fh:
                value = pickle.load(fh)
            with _CACHE_LOCK:
                _CACHE[k] = (f.stat().st_mtime, value)
            return value
        except Exception:
            return None
    return None


def _cache_put(r: dict, value) -> None:
    k = _cache_key(r)
    if k is None:
        return
    with _CACHE_LOCK:
        _CACHE[k] = (time.time(), value)
    f = _cache_file(k)
    if f is not None:
        import pickle
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "wb") as fh:
                pickle.dump(value, fh)
        except Exception:
            pass   # disk cache is a convenience; memory still has it


def _find_trace(trace_id: str) -> dict | None:
    for rec in reversed(read_all()):
        if rec.get("id") == trace_id:
            return rec
    return None
