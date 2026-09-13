# Context engineering and RAG — as implemented here

**The idea.** The model can only reason over what it is shown. Context engineering is choosing that content deliberately. RAG (retrieval-augmented generation) is the pattern: retrieve the relevant passages first, then generate only from them.

**What we built (session 2).**

*Index time — `ingest.py`.* Each policy document in `corpus/policies/manifest.yaml` that has a local file is parsed page by page (pymupdf for PDF, a tag-stripper for HTML). The text is cut into chunks that end at headings or blank lines, aim for ~260 words, never exceed ~420, and carry a 40-word overlap so a criterion split across a boundary is not lost. Every chunk keeps its page range and section heading. Chunks go into SQLite three ways: as rows (the text and metadata), into an FTS5 table (keyword search with BM25 ranking), and as a 384-number embedding vector from a small local model (`all-MiniLM-L6-v2`, no API key).

*Request time — `retrieve.py`.* Plan match turns (payer, line of business, drug) into the governing documents and a confidence score; Ohio Medicaid MCO pharmacy questions route to the state UPDL, because that is who governs them. Hybrid retrieval then runs two searches over only those documents: keyword (exact on "Emgality", "J3032") and semantic (nearest vectors to the question), and merges them with reciprocal rank fusion. The top 8 become the **context packet**, each with a citation string (payer, title, policy id, date, pages).

**What the model sees.** `prompt.py` renders the packet as numbered passages. The model cites by number. That number is what the verifier and output guardrails check.

**What to try.** Change `retrieval.top_k` or `chunking.target_words` in `config.yaml`, re-run `make ingest` (for chunking) and `make eval`, and watch whether groundedness moves. That loop is the whole craft.
