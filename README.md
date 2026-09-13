# Trailhead Rx

A patient-facing navigator that answers two questions about a prescribed medicine on one screen:

1. **What does my plan say?** Prior authorization, step therapy, required documentation, timelines, and the appeal path, cited to the payer's published policy.
2. **What is my best long-term route?** Through coverage, around it via the manufacturer (DTC pharmacy, copay card, assistance), or around it via cash and discount cards, with eligibility rules and twelve-month stability for each.

First category: migraine. First market: Ohio. Every claim is cited and dated. The app never recommends a drug and stores nothing about the person.

Trailhead Rx is also a learning project. It is built to exercise three ideas in a regulated domain: governance and guardrails, model orchestration and agentic chains, and context engineering and retrieval (RAG). Start with `docs/ARCHITECTURE.md` (the map), `docs/FLUENCY.md` (the vocabulary, mapped to files), and `docs/BRIEF.md` (the full brief). Every module's docstring names the FLUENCY term it implements.

## Status

Session two: Layer 1 runs end to end — ingest → hybrid retrieval → prompt builder → model → verifier → output guardrails → audit log — with a CLI, 14 unit tests, and 17 golden eval scenarios (12 run without a model; 5 groundedness scenarios need a live key and the ring-one documents). Layer 2 (routes), the form UI, and the card scan are later sessions.

## Run it

```bash
# once
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then paste your Anthropic API key into .env
export $(cat .env | xargs)      # or: export ANTHROPIC_API_KEY=sk-ant-...

# index the policy documents you downloaded into corpus/policies/
PYTHONPATH=src python3 -m trailheadrx ingest
PYTHONPATH=src python3 -m trailheadrx status

# ask a Layer 1 question
PYTHONPATH=src python3 -m trailheadrx ask --payer "UnitedHealthcare" --lob commercial --drug Emgality \
  "What do I have to try before my plan will cover Emgality?"

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
| Tracing | `audit.py` | audit log |
| Evals | `evals/run.py` + `evals/scenarios.yaml` | golden set, regression |
| The whole request path | `pipeline.py` | chain |

## Not in this repo

Payer policy documents, program page snapshots, the vector index, the audit log, API keys, and anything a user uploads. The manifests are committed; the documents they point to are downloaded locally.
