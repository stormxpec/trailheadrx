"""Corpus refresh: re-fetch every source, notice what changed, re-index it.

Why this exists: a plan policy or a maker's program page can change any day,
and an answer built on last month's version is wrong in a way the reader
cannot see. This job runs daily on the server (and on demand on a laptop):
for every document in the policies manifest and every program page in the
programs manifest it fetches the URL, hashes the bytes (content hashing —
compare a fingerprint, not the file), and

  - policies: if the fingerprint changed, saves the new file over the old
    one and re-indexes just that document; the manifest is not edited.
  - program pages: records the change; the programs manifest holds
    hand-verified terms (costs, eligibility) that a person must re-read, so
    the job never edits it — it tells Ben.

Every source gets a checked-on date whether or not it changed. That date is
what the page shows ("Plan documents last checked …") and what the freshness
guardrail uses: "we looked and it had not changed" is a different fact from
the plan's own effective date.

State lives in one JSON file (refresh_state.json) keyed by URL: last hash,
last checked, last changed. On the server it sits on the /data volume so it
survives deploys. No model calls; no cost beyond bandwidth.

FLUENCY.md: "freshness", "content hashing", "idempotent job".
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import yaml

from . import config
from .retrieve import load_manifest

# Some maker sites answer a plain client with 403. A normal browser string gets
# the public page; we fetch one page per source per day, which is polite use.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
TIMEOUT = 30.0


@dataclass
class Change:
    kind: str            # policy | program
    label: str           # human name
    url: str
    status: str          # changed | unchanged | failed | new
    detail: str = ""


@dataclass
class Report:
    started: str
    changes: list[Change] = field(default_factory=list)

    @property
    def changed(self) -> list[Change]:
        return [c for c in self.changes if c.status in ("changed", "new")]

    @property
    def failed(self) -> list[Change]:
        """New failures only: a source that was already failing yesterday is
        listed in the printed report as 'still failing' but does not trigger
        an email every night."""
        return [c for c in self.changes if c.status == "failed"]

    @property
    def still_failing(self) -> list[Change]:
        return [c for c in self.changes if c.status == "still_failing"]

    def text(self) -> str:
        lines = [f"Trailhead Rx corpus refresh — {self.started}",
                 f"{len(self.changes)} sources checked · {len(self.changed)} changed · {len(self.failed)} failed", ""]
        for c in self.changed:
            lines.append(f"CHANGED  [{c.kind}] {c.label}\n         {c.url}\n         {c.detail}")
        for c in self.failed:
            lines.append(f"FAILED   [{c.kind}] {c.label}\n         {c.url}\n         {c.detail}")
        for c in self.still_failing:
            lines.append(f"still failing (not emailed again) [{c.kind}] {c.label} — {c.detail}")
        if not self.changed and not self.failed:
            lines.append("Nothing changed. All sources fetched and matched their last fingerprint.")
        return "\n".join(lines)


# ---- state -------------------------------------------------------------------

def state_path() -> Path:
    override = os.environ.get("TRAILHEADRX_REFRESH_STATE")
    return Path(override) if override else config.ROOT / "corpus" / "refresh_state.json"


def load_state() -> dict:
    p = state_path()
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def last_checked() -> str | None:
    """The most recent checked-on date across policy documents, for the page."""
    dates = [v.get("checked") for v in load_state().values() if v.get("kind") == "policy" and v.get("checked")]
    return max(dates) if dates else None


def _fail(entry: dict, e: Exception) -> tuple[str, str]:
    """Record a fetch failure; say whether it is new. The same error two runs
    in a row (a site that blocks scripted clients, say) is reported once."""
    msg = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
    status = "still_failing" if entry.get("last_error", "").split(":")[0] == msg.split(":")[0] else "failed"
    entry["last_error"] = msg
    entry["last_error_on"] = date.today().isoformat()
    return status, msg


# ---- fetching ----------------------------------------------------------------

def _fetch(url: str) -> bytes:
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": UA}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def _fingerprint(data: bytes, content_type_hint: str = "") -> str:
    """Hash of the content. HTML pages carry per-request noise (nonces, timestamps);
    hashing the visible text instead of the raw bytes keeps a nightly job from
    crying wolf. PDFs are hashed as bytes."""
    if content_type_hint == "html" or data[:64].lstrip().lower().startswith((b"<!doctype", b"<html")):
        try:
            from .ingest import html_text   # the same text the indexer sees
            text = html_text(data.decode("utf-8", errors="ignore"))
        except Exception:
            text = data.decode("utf-8", errors="ignore")
        data = " ".join(text.split()).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---- the job -----------------------------------------------------------------

def refresh(reindex: bool = True, only: str | None = None) -> Report:
    """Check every source. `only` = 'policies' or 'programs' to limit."""
    today = date.today().isoformat()
    report = Report(started=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    state = load_state()
    policies_dir = config.path("policies_dir")

    if only in (None, "policies"):
        for d in load_manifest():
            url, fname = d.get("url"), d.get("file")
            if not url or not fname or d.get("status") not in ("found", None):
                continue
            label = f"{d.get('payer')} — {d.get('title')}"
            entry = state.setdefault(url, {"kind": "policy", "file": fname})
            try:
                data = _fetch(url)
                fp = _fingerprint(data, "html" if str(fname).endswith(".html") else "")
            except Exception as e:
                status, msg = _fail(entry, e)
                report.changes.append(Change("policy", label, url, status, msg))
                continue
            entry["checked"] = today
            entry.pop("last_error", None)
            fpath = Path(fname) if Path(fname).is_absolute() else policies_dir / fname
            if not fpath.exists():
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_bytes(data)
                entry.update(hash=fp, changed=today)
                if reindex:
                    _reindex(d)
                report.changes.append(Change("policy", label, url, "new", "Downloaded for the first time and indexed."))
                continue
            old = entry.get("hash") or _fingerprint(fpath.read_bytes(), "html" if str(fname).endswith(".html") else "")
            if fp != old:
                fpath.write_bytes(data)
                entry.update(hash=fp, changed=today)
                if reindex:
                    _reindex(d)
                report.changes.append(Change("policy", label, url, "changed",
                                             f"Content changed since {entry.get('checked_prev', 'the last check')}; saved and re-indexed. "
                                             f"Read it: the manifest's effective date and scope may need updating."))
            else:
                entry["hash"] = fp
                report.changes.append(Change("policy", label, url, "unchanged"))
            entry["checked_prev"] = today

    if only in (None, "programs"):
        with open(config.path("programs_manifest"), encoding="utf-8") as f:
            programs = yaml.safe_load(f).get("programs", [])
        for p in programs:
            for key in ("bridge", "copay_card", "dtc", "pap"):
                rec = p.get(key)
                url = (rec or {}).get("url") if isinstance(rec, dict) else None
                if not url:
                    continue
                label = f"{p.get('drug')} — {rec.get('name') or key}"
                entry = state.setdefault(url, {"kind": "program"})
                try:
                    fp = _fingerprint(_fetch(url), "html")
                except Exception as e:
                    status, msg = _fail(entry, e)
                    report.changes.append(Change("program", label, url, status, msg))
                    continue
                entry["checked"] = today
                entry.pop("last_error", None)
                if entry.get("hash") and fp != entry["hash"]:
                    entry.update(hash=fp, changed=today)
                    report.changes.append(Change("program", label, url, "changed",
                                                 f"Page text changed. Re-read it and update the terms and verified_on "
                                                 f"({rec.get('verified_on')}) in corpus/programs/manifest.yaml if needed."))
                else:
                    entry["hash"] = fp
                    report.changes.append(Change("program", label, url, "unchanged"))

    save_state(state)
    return report


def _reindex(doc: dict) -> None:
    """Drop one document from the index and index its new file."""
    from .ingest import ingest, remove_document
    from .retrieve import open_db
    conn = open_db()
    remove_document(conn, doc["file"])
    conn.commit()
    conn.close()
    ingest(documents=[doc])


# ---- email -------------------------------------------------------------------

def send_email(subject: str, body: str) -> bool:
    """Send through Resend's HTTP API when RESEND_API_KEY and REFRESH_EMAIL_TO
    are set; otherwise return False so the caller prints instead. The API key
    is a Fly secret, never in the repo."""
    key, to = os.environ.get("RESEND_API_KEY"), os.environ.get("REFRESH_EMAIL_TO")
    if not key or not to:
        return False
    sender = os.environ.get("REFRESH_EMAIL_FROM", "Trailhead Rx <onboarding@resend.dev>")
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}"},
                   json={"from": sender, "to": [to], "subject": subject, "text": body})
        r.raise_for_status()
    return True


def run_and_notify(only: str | None = None) -> Report:
    """The scheduled entry point: refresh, then email only when there is
    something to say (a change or a failure). A quiet night sends nothing."""
    report = refresh(only=only)
    print(report.text())
    if report.changed or report.failed:
        subject = f"Trailhead Rx: {len(report.changed)} source(s) changed, {len(report.failed)} failed"
        if not send_email(subject, report.text()):
            print("\n(email not configured: set RESEND_API_KEY and REFRESH_EMAIL_TO)")
    return report
