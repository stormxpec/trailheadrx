# Landscape notes

Who else is near this problem, what they do, and where Trailhead Rx sits. Facts as read on 2026-09-16; each has a source.

## Claimable (getclaimable.com)
Post-denial appeals for patients. Upload the denial letter and insurance details, answer health questions, and it writes a customized appeal — patient narrative, clinical evidence, and the state and federal laws that apply — then mails or faxes it to the insurer, and where useful to regulators and the employer. $39.95 flat, free for some through partners; says 80% of appeals succeed, most within 10 days; 1,000+ appeals since launch in October 2024. Covers 85+ medicines including migraine (Vyepti is a named example) and GLP-1s. Founded by Warris Bokhari (CEO), Zach Veigulis (Chief AI), Alicia Graham (COO); Iowa; ~14 people. Now embedded on GoodRx drug pages ("Did your insurance deny your Emgality?").

*Where it sits relative to us:* Claimable starts after the denial and takes the patient's health story and the denial letter; it is HIPAA-covered by design. Trailhead Rx starts before the denial and takes nothing about the person: it tells you what the plan will ask for, so the first request is built to pass, and what the routes around the plan are. Complementary, not competing: the natural hand-off is our "how do I appeal" answer pointing at a Claimable-style service once a denial exists. Their use of state and federal law in appeals is the same corpus we now hold (ORC 3901.832, Chapter 3922, ERISA/ACA) — validation that the reference-law layer matters.

## GoodRx drug page (Emgality)
The drug page now leads with the medicine's options *by insurance status* (all options / private insurance required / no private insurance required), a savings-card tab, a cash price ("More options — starting at $709.38"), and the Claimable panel. That is the closest public thing to our Layer 2, but it is organized around discounts, not around the plan's rules; it cannot say what UnitedHealthcare requires before covering Emgality, or that Aetna's step is a 56-day fill checked automatically. Our differentiation is Layer 1 (the plan's own policy, cited) plus the class comparison; GoodRx's advantage is scale, brand, and cash-price data.

## Turquoise Health
Hospital and payer price transparency built on the machine-readable files the federal rules require: benchmark pricing, contract analysis, patient estimates. Sells to organizations, not patients. Relevant to us in two ways: as a model of turning a compliance-driven public data set into a product (their MRFs are our payer policy PDFs), and as a design reference — one calm product page with four clearly named areas. Not a drug-coverage tool and not a competitor.

## Northwestern Medicine (design reference only)
Full-bleed abstract background, one bold statement, one search box, three clear actions. The lesson for us is confidence and focus: one thing to do on the page.

## Alternative funding programs (AFPs) — noted 2026-09-20
Third-party vendors contracted by self-funded employers to carve expensive specialty drugs out of coverage and steer the employee to the maker's patient assistance program, sometimes with the vendor taking a share of the savings. NASTAD's 2025 issue brief reports about 10% of surveyed employers using one and describes the catch-22: assistance programs can deny the application because the person has insurance, while the plan will not cover the drug. Several makers have sued and have begun refusing AFP-referred applications. Relevance to Trailhead Rx: this is why the "free medicine from the maker" row cannot be shown as a simple option on a commercial plan, and why the employer card (fix29) tells the reader to ask HR directly and to get a written denial. Sources: https://nastad.org/sites/default/files/2025-07/resource-afp-issue-brief-2025.pdf ; https://www.fiercehealthcare.com/payers/new-wave-middlemen-promise-savings-specialty-drugs-patients-bear-risks
