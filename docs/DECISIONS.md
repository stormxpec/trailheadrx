# Decisions

A running log of choices and why. Newest at the bottom. Append; do not rewrite.

## 2026-09-10 — Build a test app to learn three concepts
Ben wants hands-on understanding of governance/guardrails, orchestration/agentic chains, and context engineering/RAG, via something that becomes usable. Two projects in parallel to accelerate learning; this is one of them.

## 2026-09-12 — Provider PA tool → patient navigator
Started as a provider-side "what does the payer require" tool. Rejected because that space is crowded (CoverMyMeds, Develop Health, others) and payers are being pushed to expose requirements directly. A patient-facing tool that starts from the patient's plan and shows step therapy, documentation, timelines, and appeals is a gap nobody serves.

## 2026-09-12 — Add the second layer: routes around coverage
Patients' real options span the plan's policy, manufacturer programs (DTC pharmacies, copay cards, assistance), and discount-card cash for generics. Nobody presents these together at the class level; brand DTC sites are single-brand and price sites don't start from the plan. Two layers on one screen: "what your plan says" and "your best long-term route."

## 2026-09-12 — Migraine first, not MS
Higher volume, more formulaic step therapy, patients more likely to self-navigate, and real manufacturer DTC presence (LillyDirect for Emgality, PfizerForAll for Nurtec/Zavzpret). MS becomes the second category on the same engine.

## 2026-09-12 — Correction on CMS-0057-F
The 2027 Prior Authorization API mandate covers medical-benefit items and services for CMS-regulated payers; drugs are carved out and pharmacy PA stays on NCPDP ePA. Relevance to a mostly-pharmacy-benefit migraine tool is limited. The Patient Access API (live since 2021 for those payers, includes pharmacy claims) is the useful one, and is a V2 feature because it involves PHI.

## 2026-09-12 — No patient data in V1
V1 answers from public policy and program documents plus a plan match. No claims, no clinical detail, no storage. This keeps V1 outside HIPAA-covered territory and makes the guardrails testable. Card images are processed in memory and discarded.

## 2026-09-12 — Name: Trailhead Rx
Chosen over descriptive options (CoverageNav, InsuriNav) and other metaphors (Rungs, Covered Compass) because it fits the "routes" language of the results screen and stretches beyond insurance. trailheadrx.com was unregistered at RDAP check on 2026-09-12; trademark check pending.

## 2026-09-12 — Public repo, corpus stays out
Repo is public for portfolio purposes. Payer documents carry terms of use, so only manifests (source URLs, versions, dates) are committed; documents are downloaded locally. Enforced by `.gitignore`.

## 2026-09-12 — Architecture framed as three layers outside the LLM
Ben's earlier "LLM stack" framing: (1) context engineering / memory / RAG, (2) model orchestration and agentic chains, (3) cognitive governance and guardrails. Trailhead Rx maps onto it as before / around / wrap: context engineering shapes the packet the model sees, orchestration sequences the calls, governance gates inputs and outputs and keeps the record. See docs/ARCHITECTURE.md. Rendered view: https://claude.ai/code/artifact/f06efd1a-47d8-4c2e-b9ac-0e18452c91af

## 2026-09-12 — Architecture relabeled in industry vocabulary; FLUENCY.md added
Ben's goal is fluency in how modern applications use LLMs: what happens inside the model versus before, around, and after it. The diagram now shows the model's five internal steps (tokenizer → embeddings → transformer blocks → logits → sampling) as the untouchable core, and labels every outside component with its industry term (ingestion, chunking, embeddings, vector store, hybrid retrieval, prompt assembly, routing, chain, fan-out, LLM-as-judge, input/output guardrails, rules-as-code, tracing, evals). docs/FLUENCY.md maps each term to our component and gives the "how to say it" sentences.

## 2026-09-12 — Session 2: Layer 1 vertical slice
Built ingest → hybrid retrieval → router → prompt builder → verifier → output guardrails → audit, a CLI, 14 tests, 17 eval scenarios. Choices: local embeddings (all-MiniLM) plus SQLite FTS5 so the only API key is Anthropic's; no orchestration framework, plain Python on the SDK, so every step is visible; structured output (claims with passage numbers) so citations are checkable by code before any judge model runs; dry-run mode when no key is present so tests and most evals run for free. Models: claude-haiku-4-5 (router, judge), claude-sonnet-5 (draft). First eval run caught an audit-log PHI leak and a Medicaid rule wording bug; both fixed before commit.

## 2026-09-13 — First live run: verifier tuned, revise loop replaced with prune
First live request (UHC commercial, Emgality) drafted an accurate plain-language answer three times and abstained three times: each attempt failed one claim on the code-level word-overlap check (scores 0.20–0.23 vs a 0.25 threshold) before the judge model ever ran, and each redraft introduced a new borderline claim, so the loop never converged. Two changes: (1) the overlap check is now a backstop that fails only near-zero overlap (0.08); paraphrase support is the judge's call, which is what LLM-as-judge is for; (2) when a minority of claims fail and at least two pass, the pipeline prunes the failed claims and ships the rest with a warning, instead of regenerating. Redraft is reserved for broken JSON or majority failure. Lesson: rules G2 (cite everything) and G7 (plain language) pull against each other, and a verifier must be designed for paraphrase, not string matching.

## 2026-09-13 — Product stance: the patient's coverage advocate
The first live trace showed UnitedHealthcare's CGRP policy waives the non-CGRP step therapy for California members and shortens the trial to 30 days in Connecticut, Kentucky, and Mississippi, while an Ohio member must fail two preventives for two months each. Ben's call: Trailhead Rx is the patient's coverage advocate, and this is exactly the information it should surface — not only "what your plan requires of you" but "what the same plan requires of someone in another state, and why that difference exists" (state mandates, not clinical judgment). Transparency that payers would rather not offer is the product. Consequences: plan match needs the member's state, not just the payer; the answer schema gets a `variation` topic so state and plan-type differences are first-class claims with citations; the Ohio step-therapy exemption law is presented as a lever, not a footnote; and the doctor summary should tell the prescriber which exception arguments the policy itself supports.
