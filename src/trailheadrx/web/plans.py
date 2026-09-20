"""The health-plan menu and the "my plan isn't listed" path.

One menu, one choice: every payer × kind-of-plan combination we hold a
document for, plus the card names that map onto one of them (Meritain →
Aetna, self-funded; UMR → UnitedHealthcare, self-funded). A visitor cannot
pick a pair we cannot answer. If the plan is not listed, they type its
name; it is checked for personal details like any other text, matched
against the alias table, and if nothing matches the name alone is recorded
so the corpus backlog is real.

FLUENCY.md: "metadata filtering" (the menu is the plan-match filter, made
visible), "guardrails".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

from .. import config
from ..retrieve import open_db

PRETTY = {"commercial": "employer or individual plan", "marketplace": "marketplace plan",
          "medicare_advantage": "Medicare Advantage", "medicaid_mco": "Ohio Medicaid", "medicaid_ffs": "Ohio Medicaid"}
OHIO_MCOS = ("CareSource", "Molina", "Buckeye", "AmeriHealth Caritas", "Humana Healthy Horizons", "Anthem")


@dataclass
class PlanChoice:
    payer: str
    lob: str
    self_funded: bool | None = None
    label: str = ""

    @property
    def value(self) -> str:
        return f"{self.payer}|{self.lob}|{'sf' if self.self_funded else ''}"


def load_aliases() -> list[dict]:
    with open(config.path("plan_aliases"), encoding="utf-8") as f:
        return yaml.safe_load(f).get("aliases", [])


def _held() -> dict[str, set[str]]:
    """payer → kinds of plan we hold at least one indexed document for."""
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
    if any("medicaid_ffs" in k for k in kinds.values()):
        for mco in OHIO_MCOS:
            kinds.setdefault(mco, set()).add("medicaid_mco")
    return kinds


def choices() -> list[PlanChoice]:
    """The menu: held combinations first, then card-name aliases that resolve
    to a held combination (so 'Meritain' appears even though no document is
    filed under that name)."""
    held = _held()
    out: list[PlanChoice] = []
    for payer in sorted(held):
        for lob in sorted(held[payer]):
            if lob == "medicaid_ffs":
                continue
            out.append(PlanChoice(payer, lob, None, f"{payer} — {PRETTY.get(lob, lob)}"))
    for a in load_aliases():
        lob = a["lob"]
        if a["payer"] in held and lob in held[a["payer"]]:
            if any(c.payer == a["payer"] and c.lob == lob and c.label.startswith(a["name"]) for c in out):
                continue
            sf = a.get("self_funded")
            tag = "self-funded employer plan" if sf else PRETTY.get(lob, lob)
            out.append(PlanChoice(a["payer"], lob, sf, f"{a['name']} — {tag} ({a['payer']})"))
    return out


def parse(value: str) -> PlanChoice | None:
    parts = value.split("|")
    if len(parts) != 3:
        return None
    payer, lob, sf = parts
    return PlanChoice(payer, lob, True if sf == "sf" else None)


# ---- "my plan isn't listed" --------------------------------------------------

def match_alias(text: str) -> PlanChoice | None:
    t = text.lower()
    for a in load_aliases():
        if any(m in t for m in a.get("match", [])):
            return PlanChoice(a["payer"], a["lob"], a.get("self_funded"), a["name"])
    return None


def record_request(name: str) -> None:
    """Keep only the plan name and the date — nothing about the person."""
    clean = re.sub(r"\s+", " ", name).strip()[:80]
    if not clean:
        return
    p = config.path("requested_plans")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"plan": clean, "on": datetime.now(timezone.utc).date().isoformat()}) + "\n")
