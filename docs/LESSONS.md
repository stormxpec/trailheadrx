# Lessons from live runs

Things the live system taught us that no design document did. Each one was caught by a real run, a test, or an eval, and each is now handled in code. Numbered in the order they happened; newest at the bottom. The "how to say it" line is the sentence to use when describing the system.

## 1. The output token budget is a governance setting
A draft cut off at the model's output limit arrives as broken JSON and looks like a model fault. It abstained three times before we saw `output_tokens: 4000` in the trace, exactly the limit. Budget raised to 16k, `stop_reason` recorded on every call, and a cutoff retries with a shorter draft.
*How to say it:* "Every model call records its stop reason; a `max_tokens` stop is treated as a truncation, not a failure."

## 2. A verifier must be built for paraphrase
A word-overlap check failed accurate plain-language claims ("your doctor needs to show the medicine is helping" versus "positive clinical response") at 0.20–0.23 against a 0.25 threshold, and every redraft introduced a new borderline claim, so the loop never converged. Overlap is now a near-zero floor (0.08) that only catches a claim citing the wrong passage; the judge decides support; failed claims are pruned rather than the whole answer regenerated, because pruning converges and regeneration does not.
*How to say it:* "Citation checking is two-stage: a lexical floor in code, then an LLM judge for paraphrase support; pruning, not redrafting."

## 3. Fan-out over shared documents produces wrong-drug attribution
In the class comparison the Aimovig row inherited Ajovy's "fail Aimovig and Emgality first" rule, because both medicines share the same passages and semantic similarity is drug-blind. Every row is now judged for "is this requirement stated for THIS medicine," and WRONG_DRUG rows are withheld with a plain explanation.
*How to say it:* "Semantic retrieval is drug-blind, so anything fanning out over shared documents needs a per-row attribution check."

## 4. A judge needs a calibrated rubric
The first Aetna answer dropped its single most useful statement — the list of preventives, any of which for 56 days satisfies step therapy — because the judge returned PARTIAL with the reason that "in the last 2 years" is less precise than "within the past 730 days," and PARTIAL was treated as a failure. That is the paraphrase rule G7 requires, not an error. The rubric now says paraphrase, unit conversion, rounding, and describing a rule's effect rather than its mechanism are SUPPORTED; PARTIAL is only for a claim that adds a detail the passage lacks; and only NOT_SUPPORTED drops a claim. A PARTIAL claim ships with the judge's reservation visible in the trace. "Strict reviewer" produced strictness about the wrong thing.
*How to say it:* "The judge rubric is calibrated to the output style: for a plain-language product, paraphrase is supported by definition, and only contradiction or absence fails a claim."

## Also worth remembering (caught by tests and evals, not live runs)
- The audit log briefly recorded a refused question, which could have held PHI. Now the question is written to the trace only after the input guardrails pass. (Eval run, session 2.)
- Rewording the eligibility text to plain language silently broke all three eligibility eval scenarios. Evals are the regression net for wording, not just logic. (Session 3.)
- "my member id is AB12345678" passed the PHI check because the pattern only allowed ":" or "#" after the label. The first web test caught it. (Session 4.)
- A stale document warning fired on a statute because its effective date is old; laws are not policies. Reference documents are exempt from the freshness rule. (Session 3.)

## 5. If a number is a sum, the model names the parts and code adds them (fix27)
Two models given the same policy and the same instruction ("add the trial lengths plus half a month") produced 2–3 months and 4 months for the same medicine. The disagreement was not about reading — both found the 56-day trial — it was about what to include and how to round. That is a rule, and rules live in code. The extractor now returns the required tries with their lengths; `months_from_steps()` does the sum with one rounding rule for every medicine, and the page can print the arithmetic. General form: ask the model for the facts it read, never for a calculation over them.
