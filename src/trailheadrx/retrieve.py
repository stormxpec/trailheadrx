"""Plan match, hybrid retrieval, and the context packet. Runs on every request.

Plan match: given the payer, line of business, and drug the patient chose,
find the governing documents in the manifest and say how confident we are.
Confidence is high when a document for that payer/LOB/drug is found and
indexed; low or zero when the pair is not in the corpus, or the manifest
says the payer does not publish its criteria. Low confidence leads to
abstention downstream — that is a feature.

Hybrid retrieval: two searches over the matched documents only —
  keyword (SQLite FTS5, BM25 ranking), which is exact on drug names and codes;
  semantic (cosine similarity of embedding vectors), which matches meaning.
The two ranked lists are merged with reciprocal rank fusion (RRF), a simple
formula that rewards chunks appearing near the top of either list.

Context packet: the top-k merged chunks, each with its citation metadata,
plus the plan match and confidence. This is the only set of facts the model
is allowed to use.

FLUENCY.md: "hybrid retrieval", "metadata filtering", "top-k", "context packet".
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field, asdict

import numpy as np
import yaml

from . import config
from .ingest import open_db, embed, load_manifest


# --------------------------------------------------------------------------
# Drug list helpers
# --------------------------------------------------------------------------

def load_drugs() -> dict:
    with open(config.path("drugs"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def drug_names() -> dict[str, dict]:
    """Map lowercase brand and generic names → drug record (branded only)."""
    d = load_drugs()
    out: dict[str, dict] = {}
    for group in ("preventive_branded", "acute_branded"):
        for rec in d.get(group, []):
            rec = dict(rec, group=group)
            for name in (rec.get("brand"), rec.get("generic")):
                if name:
                    out[name.lower().split()[0]] = rec
    return out


def resolve_drug(name: str) -> dict | None:
    return drug_names().get(name.lower().strip().split()[0])


# --------------------------------------------------------------------------
# Plan match
# --------------------------------------------------------------------------

@dataclass
class PlanMatch:
    payer_query: str
    line_of_business: str
    drug: str
    documents: list[dict] = field(default_factory=list)   # manifest rows that govern
    indexed_files: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""


def _payer_matches(query: str, payer: str) -> bool:
    q = query.lower()
    p = payer.lower()
    # Any significant word of the query appearing in the manifest payer name.
    words = [w for w in re.split(r"[^a-z0-9]+", q) if len(w) >= 4]
    return any(w in p for w in words) or q in p


def _scope_matches(drug_rec: dict | None, drug: str, scope: str) -> bool:
    s = (scope or "").lower()
    if drug.lower() in s:
        return True
    if drug_rec:
        if (drug_rec.get("generic") or "").lower() in s:
            return True
        cls = (drug_rec.get("class") or "").lower()
        if "cgrp" in cls and "cgrp" in s:
            return True
        if "gepant" in cls and ("gepant" in s or "cgrp" in s):
            return True
        if "botulinum" in cls and "botox" in s:
            return True
        if "mab" in cls and "cgrp" in s:
            return True
    return False


def plan_match(payer: str, line_of_business: str, drug: str, conn: sqlite3.Connection | None = None) -> PlanMatch:
    pm = PlanMatch(payer, line_of_business, drug)
    rec = resolve_drug(drug)
    own = conn is None
    conn = conn or open_db()
    # Candidates come from the manifest (which also knows about documents that
    # are NOT public) plus anything already in the index that the manifest
    # does not list (tests index fixtures this way).
    docs = load_manifest()
    listed = {d.get("file") for d in docs if d.get("file")}
    for r in conn.execute("SELECT payer, line_of_business, benefit_type, scope, title, policy_id, url, "
                          "effective_or_reviewed, file, downloaded_on, status FROM documents"):
        if r["file"] not in listed:
            docs.append(dict(r))
    indexed = {r["file"] for r in conn.execute("SELECT file FROM documents")}

    # Ohio Medicaid MCO pharmacy benefit is governed by the state UPDL.
    lob = line_of_business
    candidates = []
    for d in docs:
        if not _payer_matches(payer, d["payer"]):
            # Medicaid MCO pharmacy → the UPDL row governs regardless of MCO.
            if lob == "medicaid_mco" and d["line_of_business"] == "medicaid_ffs" and _scope_matches(rec, drug, d["scope"]):
                candidates.append(d)
            continue
        if d["line_of_business"] != lob and not (lob == "medicaid_mco" and d["line_of_business"] == "medicaid_ffs"):
            continue
        if _scope_matches(rec, drug, d["scope"]):
            candidates.append(d)

    pm.documents = candidates
    pm.indexed_files = [d["file"] for d in candidates if d.get("file") and d["file"] in indexed]

    if not candidates:
        pm.confidence = 0.0
        pm.reason = f"No governing document for {payer} / {line_of_business} / {drug} is in the corpus."
    elif all(d.get("status") in ("not_public", "landing_only") for d in candidates):
        pm.confidence = 0.1
        pm.reason = f"{payer} does not publish its criteria for this benefit; only a landing page is known."
    elif not pm.indexed_files:
        pm.confidence = 0.3
        pm.reason = "A governing document is listed but has not been downloaded and indexed yet."
    else:
        pm.confidence = 0.9
        pm.reason = f"{len(pm.indexed_files)} indexed document(s) govern this payer/drug pair."
    if own:
        conn.close()
    return pm


# --------------------------------------------------------------------------
# Hybrid retrieval
# --------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    payer: str
    line_of_business: str
    benefit_type: str
    title: str
    policy_id: str
    effective_or_reviewed: str
    url: str
    file: str
    page_start: int
    page_end: int
    section: str
    text: str
    score: float
    keyword_rank: int | None
    semantic_rank: int | None

    def citation(self) -> str:
        pages = f"p. {self.page_start}" if self.page_start == self.page_end else f"pp. {self.page_start}-{self.page_end}"
        pid = f" ({self.policy_id})" if self.policy_id else ""
        return f"{self.payer} — {self.title}{pid}, {self.effective_or_reviewed}, {pages}"


def _fts_query(question: str, drug: str) -> str:
    """Build an FTS5 query: OR of the meaningful words, with the drug name
    given extra weight by repeating it. Punctuation is stripped because FTS5
    treats it as syntax."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", f"{drug} {question}") if len(w) >= 3]
    stop = {"the", "and", "for", "what", "does", "with", "that", "this", "are", "how", "will", "need", "have"}
    words = [w for w in words if w.lower() not in stop]
    if not words:
        words = [drug]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


def hybrid_retrieve(question: str, pm: PlanMatch, conn: sqlite3.Connection | None = None) -> list[RetrievedChunk]:
    if not pm.indexed_files:
        return []
    own = conn is None
    conn = conn or open_db()
    k = config.CONFIG["retrieval"]["top_k"]
    n_cand = config.CONFIG["retrieval"]["candidates"]
    rrf_k = config.CONFIG["retrieval"]["rrf_k"]

    placeholders = ",".join("?" * len(pm.indexed_files))
    doc_rows = conn.execute(f"SELECT id FROM documents WHERE file IN ({placeholders})", pm.indexed_files).fetchall()
    doc_ids = [r["id"] for r in doc_rows]
    if not doc_ids:
        return []
    dph = ",".join("?" * len(doc_ids))

    # Keyword (BM25). FTS5's rank is lower-is-better.
    kw_rows = conn.execute(
        f"SELECT c.id AS chunk_id, rank FROM chunks_fts f JOIN chunks c ON c.id=f.rowid "
        f"WHERE chunks_fts MATCH ? AND c.document_id IN ({dph}) ORDER BY rank LIMIT ?",
        (_fts_query(question, pm.drug), *doc_ids, n_cand),
    ).fetchall()
    keyword_rank = {r["chunk_id"]: i + 1 for i, r in enumerate(kw_rows)}

    # Semantic (cosine over normalized vectors; brute force is fine at this size).
    emb_rows = conn.execute(
        f"SELECT e.chunk_id, e.vector FROM embeddings e JOIN chunks c ON c.id=e.chunk_id "
        f"WHERE c.document_id IN ({dph})", doc_ids,
    ).fetchall()
    semantic_rank: dict[int, int] = {}
    if emb_rows:
        mat = np.nan_to_num(np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in emb_rows]))
        qv = embed([f"{pm.drug}: {question}"])[0]
        sims = mat @ qv
        order = np.argsort(-sims)[:n_cand]
        semantic_rank = {int(emb_rows[i]["chunk_id"]): rank + 1 for rank, i in enumerate(order)}

    # Reciprocal rank fusion.
    fused: dict[int, float] = {}
    for cid, r in keyword_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + r)
    for cid, r in semantic_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + r)
    top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
    if not top:
        return []

    cph = ",".join("?" * len(top))
    rows = conn.execute(
        f"SELECT c.id AS chunk_id, c.document_id, c.page_start, c.page_end, c.section, c.text, "
        f"d.payer, d.line_of_business, d.benefit_type, d.title, d.policy_id, d.effective_or_reviewed, d.url, d.file "
        f"FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id IN ({cph})",
        [cid for cid, _ in top],
    ).fetchall()
    by_id = {r["chunk_id"]: r for r in rows}
    out: list[RetrievedChunk] = []
    for cid, score in top:
        r = by_id[cid]
        out.append(RetrievedChunk(
            chunk_id=cid, document_id=r["document_id"], payer=r["payer"], line_of_business=r["line_of_business"],
            benefit_type=r["benefit_type"], title=r["title"] or "", policy_id=r["policy_id"] or "",
            effective_or_reviewed=r["effective_or_reviewed"] or "", url=r["url"] or "", file=r["file"],
            page_start=r["page_start"], page_end=r["page_end"], section=r["section"] or "", text=r["text"],
            score=score, keyword_rank=keyword_rank.get(cid), semantic_rank=semantic_rank.get(cid),
        ))
    if own:
        conn.close()
    return out


# --------------------------------------------------------------------------
# Context packet
# --------------------------------------------------------------------------

@dataclass
class ContextPacket:
    question: str
    drug: dict | None
    plan_match: PlanMatch
    chunks: list[RetrievedChunk]
    rules_excerpt: str

    def as_prompt_text(self) -> str:
        """Render the packet for the model: numbered passages with citation
        headers. The model cites by passage number; the verifier maps numbers
        back to chunk ids."""
        parts = []
        for i, c in enumerate(self.chunks, start=1):
            parts.append(f"[Passage {i}] {c.citation()} — section: {c.section}\n{c.text}")
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "drug": self.drug.get("brand") if self.drug else None,
            "plan_match": asdict(self.plan_match),
            "chunks": [{"n": i + 1, "chunk_id": c.chunk_id, "citation": c.citation(), "section": c.section,
                        "keyword_rank": c.keyword_rank, "semantic_rank": c.semantic_rank, "score": round(c.score, 5)}
                       for i, c in enumerate(self.chunks)],
        }


def build_packet(question: str, payer: str, line_of_business: str, drug: str, rules_excerpt: str) -> ContextPacket:
    conn = open_db()
    pm = plan_match(payer, line_of_business, drug, conn)
    chunks = hybrid_retrieve(question, pm, conn) if pm.confidence >= config.CONFIG["thresholds"]["plan_match_min_confidence"] else []
    conn.close()
    return ContextPacket(question, resolve_drug(drug), pm, chunks, rules_excerpt)
