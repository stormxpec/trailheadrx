"""Layer 2, first real version: routes around coverage, from verified records.

Reads corpus/programs/manifest.yaml (structured records with a source URL and
a verified-on date each) and renders the "other ways to get it" section for
one drug and one plan type. No model call: the facts are data, the
eligibility decisions are rules-as-code, and every line names its source
and date. Freshness (rule G6) is applied per record.

Route order is deliberate: the routes are presented in the order a patient
would use them — bridge (free now, while waiting), copay card (once
approved), direct purchase (skip the plan), assistance (if cost is the
wall). Each carries its trade-off in plain words.

FLUENCY.md: "rules-as-code", "freshness".
"""
from __future__ import annotations

from datetime import date, datetime

import yaml

from . import config
from .rules import copay_card_allowed

FRESH_DAYS = 45   # program terms change monthly; flag anything older than this


def load_programs() -> dict[str, dict]:
    with open(config.path("programs_manifest"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {p["drug"].split()[0].lower(): p for p in data.get("programs", [])}


def _age_note(rec: dict | None) -> str:
    v = (rec or {}).get("verified_on")
    if not v:
        return ""
    try:
        d = date.fromisoformat(str(v))
    except ValueError:
        return ""
    days = (date.today() - d).days
    stamp = f"checked {d.strftime('%b %d, %Y')}"
    return f"{stamp}; over {FRESH_DAYS} days old, confirm before relying on it" if days > FRESH_DAYS else stamp


def _money(s: str | None) -> str:
    return "" if not s or s == "not_found" else s


def other_routes(drug: str, line_of_business: str) -> list[str]:
    rec = load_programs().get(drug.split()[0].lower())
    if not rec:
        return [f"No maker programs for {drug} are on file yet."]
    brand, maker = rec["drug"], rec.get("maker", "the drug maker")
    if rec.get("status") == "discontinued":
        return [f"{maker} has discontinued {brand}. {rec.get('note', '')}"]

    card_ok = copay_card_allowed(line_of_business)
    lines: list[str] = []

    b = rec.get("bridge")
    if b and card_ok.allowed:
        lines.append(f"While you wait: {maker} offers {b.get('terms', '')} — ask your doctor's office to enroll you "
                     f"when they send in the request. ({_age_note(b)}; {b.get('url')})")

    c = rec.get("copay_card")
    if c:
        if card_ok.allowed:
            parts = [f"Once approved: the {c.get('name', 'savings card')} brings your cost to {_money(c.get('you_pay')) or 'a lower amount'}"]
            if _money(c.get("annual_max")):
                parts.append(f"up to {c['annual_max']}")
            if _money(c.get("expires")):
                parts.append(f"current offer through {c['expires']}")
            elig = c.get("eligibility", "")
            note = " Confidence is low on the exact amount; call to confirm." if c.get("confidence") == "low" else ""
            lines.append(f"{'; '.join(parts)}. For: {elig}.{note} ({_age_note(c)}; {c.get('url')})")
        else:
            lines.append(f"The {c.get('name', 'savings card')} is not available to you: {card_ok.reason}")

    d = rec.get("dtc")
    if d and _money(d.get("price_text")):
        lines.append(f"Skip the plan: {d['name']} — {d.get('how', '')} Price: {d['price_text']}. Trade-offs: nothing you pay "
                     f"counts toward your deductible, and a cash purchase does not count as a 'try' if you later go through "
                     f"your plan. ({_age_note(d)}; {d.get('url')})")
    elif d:
        lines.append(f"{d['name']}: {d.get('how', '')} ({_age_note(d)}; {d.get('url')})")

    p = rec.get("pap")
    if p:
        ceiling = p.get("income_ceiling_fpl_pct")
        inc = f"household income at or below {ceiling}% of the federal poverty level" if ceiling else \
              (f"income limits: {p['income_table']}" if p.get("income_table") else "income limits apply")
        pname = p.get("name") or "the maker's assistance program"
        lines.append(f"If cost is the wall: {pname} provides {brand} free to people with "
                     f"{inc}. Who qualifies: {p.get('insurance_rule', 'see the program page')}. ({_age_note(p)}; {p.get('url')})")

    if rec.get("exclusions"):
        lines.append(f"Fine print: {rec['exclusions']}.")
    if rec.get("phone"):
        lines.append(f"Questions about any of these: {maker} support line {rec['phone']}.")
    return lines
