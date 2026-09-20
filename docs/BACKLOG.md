# Backlog

Queued changes, in the order we expect to do them. Move an item to DECISIONS.md when it ships.

## Shipped in fix19 (2026-09-16)
- Intro copy on the form and access-code pages (Ben's wording). Body paragraphs sit behind "What this does".
- One health-plan menu of held payer × kind combinations; "Kind of plan" removed.
- Plan-name alias table (`data/plan_aliases.yaml`) shown in the menu where the parent is held; typed names matched against it.
- "My plan isn't listed" path: PHI-checked, alias-matched, otherwise recorded (name only) in `governance/requested_plans.jsonl`.
- Look: contour-line background, hero with an original vector illustration, a doctor and a signpost scene beside the steps.

## Queued for fix20
- Replace the hand-drawn illustrations with a licensed set if the look needs more polish before wider testing (unDraw shortlist in outputs: too-many-options / doctors-orders / decision-point).
## Later
- Side-by-side picker: choose which comparable medicines appear in the grid (today: all in the class).
- QA sweep agent: run every held payer x drug pair, judge the short version against the policy, report drift (see fix22 note).
- Optional RxBIN field to identify the pharmacy benefit manager when the card name is a TPA (card-scan era).
- Note in answers for self-funded plans that employers can customize the PBM's standard criteria.
- Medicare Advantage appeal references (42 CFR 422 Subpart M) so appeal questions on MA plans get citable law.
- Python 3.11 upgrade on Ben's machine (the venv is 3.9).
- More Ohio commercial carriers for "What if I had a different plan?": today 8 commercial payers are held (Aetna, Aetna/CVS criteria, CVS Caremark, Anthem/CarelonRx, Cigna/Express Scripts, Express Scripts, UnitedHealthcare/Optum Rx, Medical Mutual). Candidates: AultCare (Canton), SummaCare (Akron), Paramount (Toledo), and the marketplace carriers (Oscar, Ambetter/Buckeye, CareSource marketplace, Anthem/Aetna/MMO marketplace).

## Session 5 (hosting) — decided 2026-09-20
- Daily corpus refresh on the server: re-fetch every URL in both manifests, hash the content, re-ingest only what changed, stamp every record with a checked-on date (distinct from the plan's own effective date). Show "Plan documents last checked <date>" on the answer page next to the maker-programs date; the freshness guardrail (G6) keys off checked-on. Email Ben when a document changes (which document, old vs new hash, a short diff of the text) or when a fetch fails; no email on a quiet night. Sending: a transactional email API (Resend or Postmark) from the refresh job, key in the server's secrets, never in the repo.
- Precomputation / cache warming: run the held plan × medicine × preset menu nightly (or only for plans whose documents changed), store answers on disk so a restart keeps them, and diff each against the previous run to flag drift (this is the QA sweep). Start with the most-asked pairs; budget-capped like live answers.
