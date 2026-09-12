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
