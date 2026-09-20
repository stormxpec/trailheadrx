"""Ingestion, chunking, and indexing. Runs once per corpus change (index time).

What it does, in order:
  1. Reads corpus/policies/manifest.yaml and takes every document whose
     `file` field is filled in (the PDF or HTML is sitting in corpus/policies/).
  2. Parses each document to text, page by page (pymupdf for PDF; a plain
     tag-stripper for HTML), so every chunk can carry a page number.
  3. Cuts the text into section-aware chunks: it prefers to break at headings
     and blank lines, aims for ~260 words, never exceeds ~420, and carries a
     short overlap so a criterion split across a boundary is not lost.
  4. Writes documents and chunks to SQLite, builds an FTS5 keyword index
     (BM25) over the chunk text, and stores an embedding vector per chunk
     from a small local model.

FLUENCY.md: "ingestion / parsing", "chunking", "metadata", "embedding model",
"vector store".
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from . import config

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

@dataclass
class Page:
    number: int
    text: str


def parse_pdf(path: Path) -> list[Page]:
    import pymupdf  # imported here so the module loads without it for tests

    pages: list[Page] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            # "text" mode keeps reading order; tables come out as rows of text,
            # which is enough for criteria lists. Layout-aware parsing is a
            # later upgrade if evals show table criteria being missed.
            pages.append(Page(i, page.get_text("text")))
    return pages


def html_text(raw: str) -> str:
    """Visible text of an HTML document: scripts, styles, navigation and tags
    stripped. Used by the indexer and by the refresh job's fingerprint."""
    raw = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(text)


def parse_html(path: Path) -> list[Page]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    raw = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", raw)
    # Keep heading and paragraph boundaries as newlines so chunking can see them.
    raw = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|br)\s*>", "\n", raw)
    raw = re.sub(r"(?i)<h[1-6][^>]*>", "\n\n", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    # HTML has no pages; treat the whole document as page 1. Citations then
    # point to the section title instead of a page number.
    return [Page(1, text.strip())]


def parse(path: Path) -> list[Page]:
    if path.suffix.lower() == ".pdf":
        return parse_pdf(path)
    return parse_html(path)


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

HEADING_RE = re.compile(
    r"^(?:[A-Z][A-Z0-9 &/,\-()]{6,}|(?:\d+\.){1,3}\s+\S.*|[IVX]+\.\s+\S.*|"
    r"(?:Criteria|Coverage|Initial|Continuation|Reauthorization|Step Therapy|"
    r"Prior Authorization|Quantity|Exclusions|Background|References)\b.*)$"
)


@dataclass
class Chunk:
    ordinal: int
    page_start: int
    page_end: int
    section: str
    text: str


def _looks_like_heading(line: str) -> bool:
    line = line.strip()
    return 0 < len(line) < 90 and bool(HEADING_RE.match(line))


def _paragraphs(pages: list[Page]) -> list[tuple[int, str]]:
    """Yield (page_number, paragraph) pairs across the document. A paragraph
    ends at a blank line, or just before a line that looks like a heading —
    PDF text extraction often drops blank lines, so headings are the more
    reliable boundary."""
    out: list[tuple[int, str]] = []
    for p in pages:
        buf: list[str] = []
        for line in p.text.splitlines():
            if not line.strip() or _looks_like_heading(line):
                if buf:
                    out.append((p.number, "\n".join(buf).strip()))
                buf = [line] if line.strip() else []
                continue
            buf.append(line)
        if buf:
            out.append((p.number, "\n".join(buf).strip()))
    return [(n, t) for n, t in out if t]


def chunk(pages: list[Page]) -> list[Chunk]:
    target = config.CONFIG["chunking"]["target_words"]
    hard_max = config.CONFIG["chunking"]["max_words"]
    overlap = config.CONFIG["chunking"]["overlap_words"]

    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_words = 0
    buf_page_start = 1
    buf_page_end = 1
    section = "Start"
    buf_section = section   # the heading in force when the current chunk began

    def flush() -> None:
        nonlocal buf, buf_words, buf_section
        if not buf:
            return
        text = "\n".join(buf).strip()
        if text:
            chunks.append(Chunk(len(chunks), buf_page_start, buf_page_end, buf_section, text))
        buf_section = section
        # Overlap: keep the tail of this chunk as the head of the next.
        tail = " ".join(text.split()[-overlap:]) if overlap else ""
        buf = [tail] if tail else []
        buf_words = len(tail.split())

    for page_no, para in _paragraphs(pages):
        first_line = para.splitlines()[0].strip()
        is_heading = _looks_like_heading(first_line)
        words = len(para.split())

        # A heading starts a new chunk when the current one is already sizeable.
        if is_heading and buf_words >= target * 0.5:
            flush()
            buf_page_start = page_no
        if is_heading:
            section = first_line[:80]
            if buf_words <= overlap:      # chunk is only the overlap tail: it belongs to this heading
                buf_section = section

        # A single very long paragraph is split on sentence boundaries.
        if words > hard_max:
            flush()
            buf_page_start = page_no
            sentences = re.split(r"(?<=[.;:])\s+", para)
            piece: list[str] = []
            n = 0
            for s in sentences:
                piece.append(s)
                n += len(s.split())
                if n >= target:
                    buf = [" ".join(piece)]
                    buf_words = n
                    buf_page_end = page_no
                    flush()
                    piece, n = [], 0
            if piece:
                buf = [" ".join(piece)]
                buf_words = n
                buf_page_end = page_no
            continue

        if buf_words + words > hard_max or (buf_words >= target and not is_heading):
            flush()
            buf_page_start = page_no
        if not buf:
            buf_page_start = page_no
            buf_section = section
        buf.append(para)
        buf_words += words
        buf_page_end = page_no

    flush()
    return chunks


# --------------------------------------------------------------------------
# Index (SQLite: documents, chunks, FTS5, embeddings)
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  payer TEXT, line_of_business TEXT, benefit_type TEXT, scope TEXT,
  title TEXT, policy_id TEXT, url TEXT, effective_or_reviewed TEXT,
  file TEXT UNIQUE, downloaded_on TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id),
  ordinal INTEGER, page_start INTEGER, page_end INTEGER, section TEXT, text TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id),
  dim INTEGER, vector BLOB
);
"""


def open_db() -> sqlite3.Connection:
    db_path = config.path("index_db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load_manifest() -> list[dict]:
    with open(config.path("policies_manifest"), encoding="utf-8") as f:
        return yaml.safe_load(f)["documents"]


_embedder = None


def embedder():
    """The local embedding model, loaded once."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(config.model("embedding"))
    return _embedder


def embed(texts: list[str]) -> np.ndarray:
    vecs = embedder().encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def ingest(rebuild: bool = False, documents: list[dict] | None = None) -> dict:
    """Index every manifest document that has a local file. Returns counts.
    `documents` overrides the manifest (tests pass a fixture list)."""
    conn = open_db()
    if rebuild:
        conn.executescript(
            "DELETE FROM embeddings; DELETE FROM chunks; DELETE FROM chunks_fts; DELETE FROM documents;"
        )
    policies_dir = config.path("policies_dir")
    counts = {"documents": 0, "chunks": 0, "skipped_no_file": 0, "skipped_already": 0}

    for d in (documents if documents is not None else load_manifest()):
        fname = d.get("file")
        if not fname:
            counts["skipped_no_file"] += 1
            continue
        fpath = Path(fname) if Path(fname).is_absolute() else policies_dir / fname
        if not fpath.exists():
            counts["skipped_no_file"] += 1
            continue
        if conn.execute("SELECT 1 FROM documents WHERE file=?", (fname,)).fetchone():
            counts["skipped_already"] += 1
            continue

        pages = parse(fpath)
        chunks = [c for c in chunk(pages) if len(c.text.split()) >= 5]   # near-empty chunks embed to NaN
        cur = conn.execute(
            "INSERT INTO documents (payer,line_of_business,benefit_type,scope,title,policy_id,url,"
            "effective_or_reviewed,file,downloaded_on,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (d.get("payer"), d.get("line_of_business"), d.get("benefit_type"), d.get("scope"),
             d.get("title"), str(d.get("policy_id") or ""), d.get("url"),
             str(d.get("effective_or_reviewed") or ""), fname, str(d.get("downloaded_on") or ""),
             d.get("status")),
        )
        doc_id = cur.lastrowid
        ids: list[int] = []
        for c in chunks:
            cur = conn.execute(
                "INSERT INTO chunks (document_id,ordinal,page_start,page_end,section,text) VALUES (?,?,?,?,?,?)",
                (doc_id, c.ordinal, c.page_start, c.page_end, c.section, c.text),
            )
            ids.append(cur.lastrowid)
            conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cur.lastrowid, c.text))
        vecs = embed([c.text for c in chunks])
        for cid, v in zip(ids, vecs):
            conn.execute(
                "INSERT INTO embeddings (chunk_id, dim, vector) VALUES (?,?,?)",
                (cid, int(v.shape[0]), v.tobytes()),
            )
        conn.commit()
        counts["documents"] += 1
        counts["chunks"] += len(chunks)
        print(f"indexed {fname}: {len(pages)} pages → {len(chunks)} chunks")

    conn.close()
    return counts


def remove_document(conn: sqlite3.Connection, fname: str) -> int:
    """Drop one document and everything indexed from it, so a refreshed file
    can be indexed in its place. Returns the number of chunks removed."""
    row = conn.execute("SELECT id FROM documents WHERE file=?", (fname,)).fetchone()
    if not row:
        return 0
    doc_id = row["id"] if hasattr(row, "keys") else row[0]
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE document_id=?", (doc_id,))]
    for cid in ids:
        conn.execute("DELETE FROM embeddings WHERE chunk_id=?", (cid,))
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
    conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return len(ids)


def index_summary() -> list[dict]:
    conn = open_db()
    rows = conn.execute(
        "SELECT d.payer, d.line_of_business, d.benefit_type, d.file, COUNT(c.id) AS chunks "
        "FROM documents d LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.payer"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    print(json.dumps(ingest(), indent=2))
