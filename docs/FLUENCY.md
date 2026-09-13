# Fluency: the vocabulary of LLM applications, mapped to Trailhead Rx

A working glossary. Each term is the word the industry uses, what it means in one line, and which file or component in this repo is our version of it. Grows as the code lands.

## Inside the model (rented, frozen — you do not build these)

**Tokenizer.** Splits text into tokens, the units the model reads. Roughly 3/4 of a word each. *Ours: Claude's; untouchable.*

**Embeddings (inside the model).** Each token becomes a vector of numbers. Not the same as the embedding model we run for retrieval, though the idea is the same. *Ours: Claude's; untouchable.*

**Transformer blocks / attention.** The layers that relate every token to every other token in the context. Where "understanding" happens. *Ours: Claude's; untouchable.*

**Logits.** Raw scores for every possible next token. *Untouchable.*

**Sampling.** Turning scores into a pick. This is the one internal step you influence, through parameters. *Ours: `temperature` near 0 for determinism; `max_tokens`; structured output via a JSON schema when we extract criteria.*

**Context window.** The maximum tokens the model can attend to in one call. Large, but not free and not infinite; more irrelevant text makes answers worse. *Why retrieval exists.*

**Structured output.** Asking the model to fill a schema (JSON) rather than write prose, so code can validate what came back. *Ours: the extracted-criteria schema in the verifier chain.*

## Before the model: context engineering (RAG)

**Context engineering.** The discipline of controlling every token the model sees. The whole "before" layer.

**RAG (retrieval-augmented generation).** Retrieve relevant passages first, then generate only from them. The pattern this app is built on.

**Ingestion / parsing.** Turning source documents into text while keeping structure (tables). *Ours: policy PDFs → text, `corpus/policies/`, index time.*

**Chunking.** Cutting text into retrievable pieces. *Ours: section-aware, so criteria tables stay whole; each chunk tagged with payer, line of business, benefit type, effective date, page.*

**Metadata.** Structured tags on each chunk that let you filter before you search. *Ours: the manifest fields.*

**Embedding model.** A small separate model that turns a chunk (or a question) into a vector so similar meanings land near each other. *Ours: chosen in session 2; named in one config file.*

**Vector store.** Where chunk vectors live. *Ours: SQLite with a vector extension — a file, not a server.*

**Semantic search / dense retrieval.** Find chunks whose vectors are nearest the question's vector.

**Keyword search / sparse retrieval (BM25).** Exact-term matching. Essential when the term is a drug name or a code.

**Hybrid retrieval.** Both of the above, merged. *Ours: yes — "Emgality" and "J3032" must match exactly, criteria language must match by meaning.*

**Reciprocal rank fusion (RRF).** How the two ranked lists become one: each passage scores 1/(60 + rank) from each list, summed, so a passage both searches found beats a passage one search loved. *Ours: yes, in `retrieve.hybrid_retrieve`. Worked numbers from Ben's UHC run are in `docs/concepts/weighting-and-scoring.md`.*

**Metadata filtering.** Restrict search to chunks whose tags match. *Ours: only the patient's matched plan.*

**Reranking.** A second, more careful pass that reorders the top candidates. *Ours: optional; added if evals show retrieval misses.*

**Top-k.** The number of chunks you keep. *Ours: tuned by evals.*

**Context packet.** Our name for the assembled bundle: top-k chunks with metadata, the program record, plan match with confidence, the applicable rules excerpt. The only facts the model may use.

**System prompt.** The standing instructions sent with every call (role, rules, format). *Ours: built from `governance/rules.yaml`.*

**Prompt assembly.** Combining system prompt, packet, and question into the final call. *Ours: the prompt builder.*

## Around the model: orchestration and agentic chains

**Orchestration.** The code that decides how many model calls to make, in what order, with what in between. *Ours: plain Python on the Anthropic SDK; no framework.*

**Chain.** A fixed sequence of steps. *Ours: retrieve → draft → verify → revise.*

**Routing.** A cheap classification call that picks which chain to run. *Ours: lookup / compare routes / checklist / out of scope.*

**Fan-out.** Several calls or retrievals in parallel, then merged. *Ours: one retriever per route for Layer 2.*

**Tool use / function calling.** The model asks your code to run a function and gets the result back. *Ours: held in reserve; harder to govern.*

**Agent.** A loop where the model decides its own next step. *Ours: deliberately not in V1.*

**Verifier / LLM-as-judge.** A second model call whose only job is to grade the first one. *Ours: the verifier, paired with code checks.*

**Model tiering.** Small fast model for easy steps, strong model for hard ones. *Ours: router and extraction on the small tier; synthesis and verification on the strong tier.*

**Frameworks (LangChain, LlamaIndex).** Libraries that package these patterns. *Ours: none, so every step stays visible.*

## After the model, and wrapping it: governance and guardrails

**Guardrails.** Checks at the edges. Input guardrails (what's allowed in) and output guardrails (what's allowed out).

**PHI detection.** Screening free text for protected health information. *Ours: input guardrail; refuses and stores nothing.*

**Groundedness.** Every claim traces to a retrieved passage. *Ours: output guardrail; a claim without a citation is blocked.*

**Citation.** The pointer from a claim to its source: payer, document, version, page.

**Abstention.** Declining to answer when confidence is low or the source isn't in the corpus. *Ours: an outcome, not an error; the four non-public payers abstain by design.*

**Rules-as-code.** Non-negotiable logic written as ordinary code, not left to the model. *Ours: the eligibility engine — Medicare/Medicaid and copay cards, income thresholds, Ohio step-therapy law versus plan type.*

**Tracing / observability.** Recording every request end to end: inputs, retrieved chunks, draft, verdict, model, latency, cost. *Ours: the audit log, retaining nothing about the person.*

**Evals.** The test suite: a golden set of scenarios with expected answers, scored automatically. *Ours: fifty scenarios, plan × drug × situation.*

**Regression.** A change that quietly makes results worse. *Evals exist to catch it; a change that lowers a score does not ship.*

**Freshness.** How recently a source was verified. *Ours: a flag past 30 days.*

## How to say it

**RAG layer.** "My RAG layer parses payer PDFs with section-aware chunking, stores embeddings in SQLite, and uses hybrid retrieval with metadata filtering so the model only sees passages from the patient's own plan."

**Orchestration.** "My orchestration layer routes each request with a small model, runs a draft-verify-revise chain against a rules file, and fans out one retriever per route; the strong model is reserved for synthesis and verification."

**Governance.** "My governance layer enforces eligibility in code, requires a citation on every claim or abstains, screens inputs for PHI, traces every request end to end, and runs a fifty-scenario eval suite before any change ships."

**In one breath.** "Trailhead Rx is a RAG application over Ohio payer policies, with a form on the front, a hybrid-retrieval context layer that builds a packet from the patient's own plan, a routed draft-verify-revise chain around Claude, and a governance wrap of input and output guardrails, rules-as-code, tracing, and evals."
