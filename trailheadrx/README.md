# Trailhead Rx

A patient-facing navigator that answers two questions about a prescribed medicine on one screen:

1. **What does my plan say?** Prior authorization, step therapy, required documentation, timelines, and the appeal path, cited to the payer's published policy.
2. **What is my best long-term route?** Through coverage, around it via the manufacturer (DTC pharmacy, copay card, assistance), or around it via cash and discount cards, with eligibility rules and twelve-month stability for each.

First category: migraine. First market: Ohio. Every claim is cited and dated. The app never recommends a drug and stores nothing about the person.

Trailhead Rx is also a learning project. It is built to exercise three ideas in a regulated domain: governance and guardrails, model orchestration and agentic chains, and context engineering and retrieval (RAG). See `docs/concepts/` for a plain-English explanation of each as implemented here, and `docs/BRIEF.md` for the full project brief.

## Status

Session one: repo scaffold, migraine drug list, ring-one policy manifest. Nothing runs yet.

## Layout

```
docs/            Brief, decisions log, concept explainers
data/            Drug list and other reference data (committed)
corpus/          Payer policies and program records (NOT committed — see .gitignore)
governance/      Rules file and audit log
evals/           Golden scenarios and the scorer
src/trailheadrx/ Application code
```

## Not in this repo

Payer policy documents, program page snapshots, the vector index, API keys, and anything a user uploads. The `corpus/` manifests are committed; the documents they point to are downloaded locally.
