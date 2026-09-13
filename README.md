# Trailhead Rx

A patient-facing navigator that answers two questions about a prescribed medicine on one screen:

1. **What does my plan say?** Prior authorization, step therapy, required documentation, timelines, and the appeal path, cited to the payer's published policy.
2. **What is my best long-term route?** Through coverage, around it via the manufacturer (DTC pharmacy, copay card, assistance), or around it via cash and discount cards, with eligibility rules and twelve-month stability for each.

First category: migraine. First market: Ohio. Every claim is cited and dated. The app never recommends a drug and stores nothing about the person.

Trailhead Rx is also a learning project. It is built to exercise three ideas in a regulated domain: governance and guardrails, model orchestration and agentic chains, and context engineering and retrieval (RAG). Start with `docs/ARCHITECTURE.md` (the map), `docs/FLUENCY.md` (the vocabulary, mapped to files), and `docs/BRIEF.md` (the full brief). Every module's docstring names the FLUENCY term it implements.

## Status

Session three: both layers run end to end from the command line. Layer 1 (what your plan says) — ingest → hybrid retrieval → prompt builder → model → verifier → output guardrails → audit log — answers in plain language with a wait estimate, a class comparison table (`--compare-class`), and, for appeal questions, citations to Ohio law (ORC 3901.832 step-therapy exemption, ORC Chapter 3922 external review). Layer 2 (other ways to get it) renders verified manufacturer program records from `corpus/programs/manifest.yaml` with rules-as-code eligibility, so a Medicare member is told the copay card is off-limits and why. 17 unit tests, 17 eval scenarios. The form UI and hosting are session 4/5.

## Run it

```bash
# once
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then paste your Anthropic API key into .env
export $(cat .env | xargs)      # or: export ANTHROPIC_API_KEY=sk-ant-...

# download the Ohio law pages (public domain; the payer PDFs you fetch by hand — see the manifest)
cd corpus/policies
curl -sSL -o ohio-orc-3901-832.html https://codes.ohio.gov/ohio-revised-code/section-3901.832
curl -sSL -o ohio-orc-3922-02.html  https://codes.ohio.gov/ohio-revised-code/section-3922.02
curl -sSL -o ohio-orc-3922-08.html  https://codes.ohio.gov/ohio-revised-code/section-3922.08
curl -sSL -o ohio-orc-3922-09.html  https://codes.ohio.gov/ohio-revised-code/section-3922.09
cd ../..

# index the documents in corpus/policies/
PYTHONPATH=src python3 -m trailheadrx ingest
PYTHONPATH=src python3 -m trailheadrx status

# ask a question (Layer 1 answer + Layer 2 routes)
PYTHONPATH=src python3 -m trailheadrx ask --payer "UnitedHealthcare" --lob commercial --drug Emgality \
  "What do I have to try before my plan will cover Emgality?"

# the same, plus a coverage-path table for every medicine in the class
PYTHONPATH=src python3 -m trailheadrx ask --payer "UnitedHealthcare" --lob commercial --drug Emgality \
  --compare-class "What do I have to try before my plan will cover Emgality?"

# an appeal question pulls in the Ohio law pages as citable passages
PYTHONPATH=src python3 -m trailheadrx ask --payer "UnitedHealthcare" --lob commercial --drug Emgality \
  "My plan denied Emgality. How do I appeal, and how long do they have to answer?"

# tests (no model calls) and evals (the scorecard)
python3 -m pytest -q tests
PYTHONPATH=src python3 evals/run.py
```

Without an API key the app runs in **dry-run** mode: every model call returns a labeled stand-in, so ingestion, retrieval, guardrails, verification, and logging all work and can be tested for free. Answers in dry-run are marked and never meant for a patient.

`make ingest`, `make status`, `make test`, `make eval` are shortcuts for the above.

## Layout

```
config.yaml          models, paths, chunking, retrieval, thresholds — the one place to tune
docs/                ARCHITECTURE (map), FLUENCY (vocabulary), BRIEF, DECISIONS, concepts/
data/                drugs_migraine.yaml — the migraine drug list
corpus/policies/     manifest.yaml (committed) + downloaded documents (NOT committed)
corpus/programs/     manufacturer / assistance program records (session 3)
governance/          rules.yaml (rules + system prompt + eligibility data); audit/ (NOT committed)
evals/               scenarios.yaml (golden set) and run.py (scorer)
tests/               unit tests; fixtures/ holds a synthetic policy PDF, clearly labeled
src/trailheadrx/     the application — see "Which file is which box" below
```

## Which file is which box

| Box in ARCHITECTURE.md | File | FLUENCY term |
|---|---|---|
| Input guardrails | `guardrails.py` | PHI detection, scope check |
| Plan match, hybrid retrieval, context packet | `retrieve.py` | metadata filtering, hybrid retrieval, top-k |
| Ingestion & chunking, embeddings → vector store | `ingest.py` | parsing, chunking, embedding model, vector store (SQLite + FTS5) |
| Router, prompt builder | `prompt.py` | routing, prompt assembly, structured output |
| Inside the model | `llm.py` | model tiering, sampling, dry-run |
| Verifier | `verify.py` | LLM-as-judge, groundedness |
| Output guardrails | `guardrails.py` | citation check, abstention, freshness, framing |
| Rules-as-code | `rules.py` + `governance/rules.yaml` | eligibility engine |
| Other ways to get it (Layer 2) | `programs.py` + `corpus/programs/manifest.yaml` | rules-as-code, freshness |
| Class comparison | `compare.py` | fan-out, per-row LLM-as-judge |
| Tracing | `audit.py` | audit log |
| Evals | `evals/run.py` + `evals/scenarios.yaml` | golden set, regression |
| The whole request path | `pipeline.py` | chain |

## Not in this repo

Payer policy documents, program page snapshots, the vector index, the audit log, API keys, and anything a user uploads. The manifests are committed; the documents they point to are downloaded locally.
