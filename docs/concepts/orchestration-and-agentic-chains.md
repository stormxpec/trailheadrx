# Orchestration and agentic chains — as implemented here

**The idea.** No single model call does the job. Orchestration is the code that decides how many calls to make, in what order, and what runs between them.

**What we built (session 2).** `pipeline.py` is one fixed chain:

input guardrails → plan match → hybrid retrieval → **router** → **prompt builder** → model → **verifier** → (revise, up to 2×) → output guardrails → audit.

*Router — `prompt.route`.* A one-word classification by the small model (Haiku): lookup, compare_routes, checklist, out_of_scope. In dry-run it falls back to keyword rules.

*Prompt builder — `prompt.draft`.* The only component that calls the strong model (Sonnet). It combines the system prompt (the rules block from `governance/rules.yaml`, verbatim), the packet, the question type, and the answer schema. The schema forces structured output: a list of claims, each with passage numbers. On a failed verification it is called again with the verifier's notes appended — that is the "revise" arrow.

*Verifier — `verify.py`.* Code checks first (valid JSON, every claim cites an existing passage, cited passage shares enough distinctive words with the claim), then LLM-as-judge (Haiku grades each claim SUPPORTED / PARTIAL / NOT_SUPPORTED against its cited passage only). Anything not SUPPORTED becomes a revision note.

*Model tiering — `config.yaml`.* Small tier for routing and judging, strong tier for drafting. Named in one place.

**Not built yet, on purpose.** Fan-out for Layer 2 (session 3), tool use, and agent loops. Each adds capability and subtracts predictability; we add them when an eval says we need them.
