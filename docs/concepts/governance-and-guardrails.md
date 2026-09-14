# Governance and guardrails — as implemented here

**The idea.** The model's judgment is never the last line of defense. Rules are written once in a readable file and enforced by code at the edges, every request is recorded, and a test suite decides whether a change ships.

**What we built (session 2).**

*Rules file — `governance/rules.yaml`.* Eight rules (G1–G8), each with its statement, how it is enforced, and which module enforces it. The `system_prompt_rules` block is inserted verbatim into every model prompt. The `eligibility` block is data for the rules engine.

*Input guardrails — `guardrails.py`.* PHI detection (patterns for SSNs, dates of birth, member IDs, phone, email, addresses, plus clinical cues like "I was diagnosed"), scope check (drug must be on our list; "which drug is best" is refused). A refusal stores only the category, never the text.

*Rules-as-code — `rules.py`.* Copay cards are blocked for Medicare and Medicaid members; Ohio's step-therapy exemption law applies to state-regulated plans and Medicaid but not self-funded employer plans or Medicare; assistance programs use an income ceiling. Ordinary functions with ordinary tests. The model never decides these.

*Output guardrails — `guardrails.py`.* Claims without a valid passage citation are removed; recommendation language blocks the answer; every answer carries the "published policy, not a decision" framing and freshness warnings for old sources.

*Tracing — `audit.py`.* One JSON line per request: guardrail outcomes, plan match, retrieved chunk ids and citations, draft, verifier notes, model IDs, tokens, latency, estimated cost. The question is written only after the input guardrails pass.

*Evals — `evals/`.* Seventeen golden scenarios across abstention, PHI refusal, scope refusal, eligibility, and groundedness. `make eval` prints a scorecard by category and writes `evals/last_run.json`.

**What the evals caught on day one.** The first run failed two scenarios: the audit log was recording the refused question (so PHI leaked into the trace), and the Ohio law rule hedged about self-funding for a Medicaid member. Both were fixed in `pipeline.py` and `rules.py`, and the unit test was tightened to check the whole trace record. That is the loop working as designed.

## Governance at the edge (session 4)

When the pipeline runs as a website, three more gates sit in front of it, all in `src/trailheadrx/web/gate.py`, all ordinary code with tests:

- **Access code.** One shared phrase from `.env`. A correct entry sets a cookie holding an HMAC signature — not the code — so changing the code logs everyone out. Wrong entries are counted per visitor and locked after ten in an hour; the text typed is never written anywhere.
- **Rate limit.** Questions per visitor (by IP) per hour, in memory. `config.yaml → web.rate_limit_per_hour`.
- **Spend cap.** The audit trace already records an estimated cost for every model call. The gate sums today's records, adds a reservation for answers still running, and refuses new questions above `web.daily_spend_cap_usd`. In dry-run mode the cap never bites because nothing costs anything.

Order matters: cookie → rate limit → spend cap → input guardrails → queue. A question that would be refused for PHI is refused before it spends a worker, and the pipeline runs the same guardrails again on its own — the site never relies on the edge alone.

