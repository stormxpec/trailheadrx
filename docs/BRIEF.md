# Trailhead Rx — Project Brief (v0.3)

Name chosen Sep 12, 2026: Trailhead Rx (register trailheadrx.com; confirm trademark clearance).
Status: concept reset on Sep 12, 2026 from a provider PA tool to a patient-facing, category-level coverage and access navigator. Migraine first. Remaining *assumptions* are marked.

## The problem

A person prescribed a migraine drug today has to work out, on their own, four things nobody presents together: what their health plan will require before covering it (step therapy, documentation, prior authorization, appeal rights); what the manufacturer offers directly (cash-price DTC pharmacies such as LillyDirect and PfizerForAll, copay cards, patient assistance programs); what the cheap generic rungs of the ladder cost with a discount card; and how those routes interact over a year (a copay card that resets in January, an accumulator program that quietly stops counting it, a cash purchase that never counts toward step therapy). Brand DTC sites show one brand. Discount sites show one price. Plan portals show one policy, badly. The result is that patients discover the rules from a denial letter, and choose a route on month-one cost rather than twelve-month reality.

The Navigator sits at the class level, starts from the patient's plan, and answers two questions on one screen: **"What does my plan say?"** and **"What is my best long-term route to this medicine?"** Every claim is cited to a published source with a verified-on date, and the app never advises on which drug to take.

## Who it's for

Primary user: an adult in Ohio whose neurologist or PCP has recommended a migraine preventive or acute treatment, especially a CGRP-class drug, and who has commercial, Marketplace, Medicaid managed care, or Medicare Advantage coverage. Also the family member who does the navigating for them.

Portfolio audience: Ben's case that patient-first access navigation is a category-level product, not a brand feature. The natural commercial homes later are advocacy organizations, employers, manufacturer patient-services teams, and health systems' neurology practices, none of which V1 has to serve.

## Version 1 scope

Category: migraine only. Preventives (CGRP monoclonals: Aimovig, Ajovy, Emgality, Vyepti; oral gepants: Nurtec, Qulipta; and the older generic preventives that make up the step-therapy rungs) and acute treatments (gepants, ditans, triptans, Zavzpret). MS and other neurology classes are later categories on the same engine.

Payers: all plans operating in Ohio, built in rings. Ring one, verified by hand: Anthem/Elevance, Aetna, UnitedHealthcare, Cigna, Medical Mutual, CareSource, Molina, Buckeye (Centene), AmeriHealth Caritas, Humana MA, Ohio Medicaid FFS, and the major PBMs whose criteria govern the pharmacy benefit (Express Scripts, CVS Caremark, Optum Rx, Prime). Ring two: remaining Ohio carriers, Marketplace-only plans, and formulary variants.

Manufacturer and access programs: for each drug, the manufacturer's DTC pharmacy option if one exists, copay card terms, patient assistance program eligibility, and any bridge or free-trial program, all from public program pages. Discount-card cash prices for the generic rungs from sources whose terms permit it (no scraping of sites that prohibit it).

On-ramp: photograph the insurance card, or pick the plan from a list. The card scan extracts carrier, plan family, and the pharmacy routing fields (RxBIN/PCN/RxGroup, which identify the PBM). Because a card cannot reveal whether a plan is self-funded or which formulary variant an employer chose, the app confirms with two or three plain questions and labels the match with a confidence level. The card image is processed in memory and discarded, never stored.

What the patient sees, on one screen:

**Layer one, what your plan says.** Whether the drug needs prior authorization; the step therapy ladder in plain language (which drugs, how many, how long counts as an adequate trial); what the prescriber must document; expected decision timeline; whether Ohio's step-therapy exception law applies to this plan type (it protects state-regulated plans, not self-funded employer plans); and the appeal path from internal appeal through Ohio external review. Each item cited to payer, document, version, page.

**Layer two, your routes.** Three routes, side by side: through coverage (what it takes, what happens when it's approved, what it typically costs at the plan's tier); around coverage via the manufacturer (DTC cash price, copay card terms, assistance eligibility); around coverage via cash and discount cards (mainly for generic rungs). For each: who is eligible, what it costs at list or cash, how stable it is over twelve months, and how choosing it affects the other routes (for example: a cash purchase does not count toward step therapy; a copay card cannot be used by Medicare or Medicaid members; an accumulator program can leave the deductible unmet mid-year).

**A one-page summary to bring to the doctor**, listing what the office would need to document to move through or around the ladder.

Explicit non-goals for V1: recommending a drug; computing a patient-specific dollar figure (that requires benefit design, which is V2 via benefit summaries or the Patient Access API); submitting anything to a payer; storing any patient information; any category other than migraine.

## How the three concepts show up

| Concept | Where it lives |
|---|---|
| **Context engineering & memory (RAG)** | Two corpora with different shapes. Policy corpus: section-aware chunking of payer and PBM PDFs, metadata on every chunk (payer, line of business, benefit type, drug, effective date, version, source, page), hybrid retrieval (exact match for drug names and codes plus semantic search for criteria language), document versioning. Programs corpus: structured records extracted from manufacturer and assistance-program pages, with eligibility fields and a verified-on date, re-checked on a schedule because these change constantly. Plan matching: a mapping layer from card fields and confirmation answers to the governing documents, with explicit confidence. |
| **Model orchestration & agentic chains** | Router classifies the request. Plan-match chain: card OCR → field extraction → candidate policies → confirmation questions → best match with confidence. Layer-one chain: retrieve → extract criteria into a fixed schema → plain-language rewrite → verifier checks every claim has a citation. Layer-two chain: parallel retrievers for coverage, manufacturer, and cash routes → eligibility filter (rules engine, not a model) → twelve-month stability assessment → synthesizer → verifier. Cheaper models for OCR cleanup, routing, extraction; strongest model for synthesis and verification. |
| **Governance & guardrails** | Rules live in a readable policy file, not buried in prompts. Hard rules: no drug recommendations, ever; no claim without a citation and verified-on date; abstain when the plan match is low-confidence or the drug/plan pair is not in corpus; PHI detector refuses pasted clinical detail and the app stores nothing about the person; eligibility rules (Medicare/Medicaid and copay cards, income thresholds, plan type and Ohio law) enforced by code, not by the model; every option carries a freshness warning past a threshold; plain-language output at roughly an eighth-grade reading level; disclosure of any referral or affiliate relationship if one ever exists. Full audit log per query. A golden set of scenarios (plan × drug × patient situation) with expected answers, run on every change. |

## What "working" means for V1

A golden set of at least 50 scenarios across ring-one plans and the migraine drug list. Targets (*assumption*, to tune): at least 90% of layer-one answers correctly cited; 100% abstention when the plan/drug pair is not in corpus; 100% refusal on PHI inputs; 100% correct application of the eligibility rules (Medicare and copay cards is the canary); every layer-two option showing a verified-on date under 30 days old; card scan producing a correct plan family match on at least 80% of a test set of sample cards; a complete audit record for every query.

## Architecture (plain English)

Python application calling the Claude API. `corpus/policies/` holds payer and PBM documents with a manifest; `corpus/programs/` holds structured program records with source URLs and verified-on dates; a scheduled job re-checks program pages and flags changes. A local vector index (SQLite-backed) for policies. A rules module for eligibility, written to be readable by a non-developer. A small web interface: card upload or plan picker, drug picker, one results screen with the two layers and the doctor summary. `evals/` with the scenario set and scorer; `governance/` with the rules file and audit log. `CLAUDE.md`, `DECISIONS.md`, and `docs/concepts/` so the repo doubles as Ben's study guide.

Hosting: laptop first, then a public demo behind an access code, with rate limits, a monthly API spend cap, a visible "demonstration, not medical or coverage advice" banner, and no retention of uploaded images or free-text inputs beyond the audit trail. Target (*assumption*): a small container on Fly.io, Render, or Railway with the index shipped alongside the app.

## Build plan (*assumption*: five working sessions)

Session 1: migraine drug list and ring-one policy manifest; download and ingest policies; layer one working end to end for one plan and one drug, with citations.
Session 2: governance layer — rules file, abstention, PHI gate, eligibility rules engine, audit log, first 20 scenarios and the scorer.
Session 3: programs corpus — manufacturer and assistance records for every migraine drug, freshness job, layer two for one plan.
Session 4: orchestration — router, plan-match chain with confidence, parallel route retrievers, verifier, model tiering; expand to full ring one.
Session 5: card scan on-ramp, interface, hosting with access code and spend cap, remaining scenarios, portfolio write-up and demo script.
After: Patient Access API connection (claims history shows where a patient sits on the ladder; PHI, so it gets its own governance review), benefit-summary parsing for dollar estimates, MS as the second category.

## Risks

Program terms change monthly, so freshness is core, not polish. Card scanning is probabilistic and a wrong plan match is worse than no match, hence the confirmation questions and confidence labels. GoodRx and similar sites prohibit scraping; the app uses sources that permit it and links out otherwise. Employer self-funded plans, which cover most working-age patients, often use custom formularies that aren't public, so the app must be honest about "best available match" rather than pretend precision. The regulatory line is clear as long as V1 stores nothing about the person and recommends no drug, and both are treated as launch blockers. And the same core risk as before: a confident, cited, wrong answer is the failure mode, so the verifier and the golden set are not optional.

## Decided

Patient-facing, migraine first, all Ohio plans in rings, card scan on-ramp, two-layer results screen, form-style interface, public demo behind an access code, no patient data stored in V1.

## Open

1. Whether the first demo scenarios should be built around one or two named plans (e.g., Anthem commercial and CareSource Medicaid) to make the story concrete.
