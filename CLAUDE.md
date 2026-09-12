# CLAUDE.md — project context for Claude Code

## What this is
Trailhead Rx: a patient-facing coverage and access navigator. Migraine first, Ohio first. Read `docs/BRIEF.md` before doing anything substantive. Decisions and their reasons live in `docs/DECISIONS.md`; append there when a decision is made, do not rewrite history.

## Who is building it
Ben is the product owner and is not a developer. Explain choices in plain English. Prefer boring, readable code over clever code. Every module should have a short docstring saying what it does and why it exists. When adding a concept (a retriever, a guardrail, a chain), add or update the matching page in `docs/concepts/`.

## Hard rules (also encoded in governance/rules.yaml)
- Never recommend a drug or compare clinical effectiveness.
- Every factual claim to the user carries a citation (payer, document, version, page) or a program source URL with a verified-on date.
- If the plan/drug pair is not in the corpus, or plan match confidence is low, abstain and say so. Never fill gaps from general knowledge.
- Eligibility rules (Medicare/Medicaid and copay cards, income thresholds, plan type vs. Ohio step-therapy law) are enforced in code, not left to the model.
- Store nothing about the person. Card images are processed in memory and discarded. Free-text inputs are checked for PHI and refused if found.
- This repo is public. Never commit corpus documents, indexes, keys, or uploads. Check `.gitignore` before adding new data paths.

## Stack
Python 3.11+, Anthropic SDK, SQLite-backed local vector index, a small web UI (framework TBD in session 2). Keep dependencies minimal and pinned in `requirements.txt`.

## Conventions
- Data files: YAML, one concept per file, with a `_meta` block (source, verified_on, notes).
- Model tiering: use the cheapest model that passes the eval for routing/extraction; reserve the strongest model for synthesis and verification. Name the model in one config file, never inline.
- Tests and evals run with a single command (`make eval` once it exists). Do not merge changes that lower the eval scores without a note in DECISIONS.md.
