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

IN_FLIGHT_RESERVE_USD = 0.25   # assumed cost of a job that has not finished yet (answer + comparison)


@dataclass
class Job:
    id: str
    request: dict
    status: str = "queued"          # queued | running | done | failed
    created: float = field(default_factory=time.time)
    answer: object = None           # pipeline.Answer
    table: tuple | None = None      # (class name, rows) when compare was requested
    trace: dict | None = None       # the audit record for this answer
    error: str = ""


class JobRunner:
    def __init__(self, workers: int | None = None):
        w = config.CONFIG.get("web", {})
        self.ttl = 60 * int(w.get("job_ttl_minutes", 60))
        self._pool = ThreadPoolExecutor(max_workers=workers or int(w.get("job_workers", 2)))
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
        from ..pipeline import answer
        job.status = "running"
        r = job.request
        try:
            job.answer = answer(r["question"], r["payer"], r["lob"], r["drug"],
                                self_funded=r.get("self_funded"), use_judge=True)
            if r.get("compare") and job.answer.outcome == "answered":
                from ..compare import compare_class
                name, rows, _calls = compare_class(r["drug"], r["payer"], r["lob"])
                job.table = (name, rows)
            job.trace = _find_trace(job.answer.trace_id)
            job.status = "done"
        except Exception as e:   # the page must never hang on an exception in a worker
            job.error = f"{type(e).__name__}: {e}"
            job.status = "failed"


def _find_trace(trace_id: str) -> dict | None:
    for rec in reversed(read_all()):
        if rec.get("id") == trace_id:
            return rec
    return None
