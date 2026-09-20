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

## Shipped in session 5 (2026-09-20)
- Fly.io hosting at trailheadrx.com; daily corpus refresh with email on change; nightly precompute/QA sweep; question-aware emphasis with the appeal row. Still open from that list: verify a sending domain in Resend so mail comes from @trailheadrx.com.
