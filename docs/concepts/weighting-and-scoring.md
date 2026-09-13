# Weighting and scoring: how a passage earns its place, and how a claim earns its citation

Three places in Trailhead Rx turn text into numbers and then rank or gate on those numbers. None of them is the model. Knowing where each score comes from is what lets you say "my retrieval layer fuses BM25 and cosine with RRF" and mean it.

| Where | What is scored | Score used for | Code |
|---|---|---|---|
| Keyword search | every chunk vs. the question's words | rank list #1 | `retrieve._fts_query`, SQLite FTS5 |
| Semantic search | every chunk vs. the question's meaning | rank list #2 | `retrieve.hybrid_retrieve`, all-MiniLM |
| Fusion | the two rank lists | the 8 passages in the packet | `retrieve.hybrid_retrieve` (RRF) |
| Verifier backstop | each claim vs. the passage it cites | fail near-zero overlap | `verify.code_checks` |
| Judge | each claim vs. its passage | SUPPORTED / NOT | `verify.llm_judge` (haiku) |

## 1. Keyword search (BM25 through FTS5)

The question and the drug name are split into words, short words and stopwords are dropped, and what is left becomes an OR query. For Ben's live run:

> *"What does UnitedHealthcare require before covering Emgality?"* with drug Emgality

becomes, after `_fts_query`:

```
"Emgality" OR "UnitedHealthcare" OR "require" OR "before" OR "covering"
```

Two things to notice. The drug name is put in front of the question so it is always present, and FTS5 scores a chunk higher when it matches more of these words and when a matched word is rare across the corpus (that is what BM25 does: term frequency × inverse document frequency, with a length penalty). "Emgality" appears in only a few chunks of the UHC document, so it is worth far more than "require," which appears everywhere. This is why the keyword list is dominated by the chunks that actually name the drug.

Weakness: a chunk that says "galcanezumab" but not "Emgality" scores zero here. Keyword search does not know synonyms.

## 2. Semantic search (embeddings and cosine similarity)

Every chunk was embedded at ingest time into a 384-number vector by all-MiniLM-L6-v2. At query time the same model embeds the string `"Emgality: What does UnitedHealthcare require before covering Emgality?"`. Both sides are unit-length, so a dot product is the cosine similarity: 1.0 means identical direction, 0 means unrelated. Every chunk in the governing documents is scored (brute force; the corpus is small) and the top 30 become the semantic rank list.

Strength: a passage about "positive clinical response, defined as reduction in monthly migraine days" ranks well for a question about "how do I show it's working," even with no shared words. Weakness: it happily ranks a passage about Ajovy's requirements high for an Emgality question, because the two are semantically near-identical. That weakness is exactly what produced the wrong-drug row in the class comparison; the per-row judge exists because of it.

## 3. Fusion: reciprocal rank fusion (RRF)

Now there are two lists, each with its own kind of score (BM25 in the tens, cosine between −1 and 1). They cannot be added directly, so Trailhead Rx throws the raw scores away and keeps only the ranks. Each chunk's fused score is

```
score = 1 / (60 + keyword_rank) + 1 / (60 + semantic_rank)
```

with a missing rank contributing nothing. The constant 60 (`rrf_k` in `config.yaml`) flattens the curve so that being 1st versus 3rd matters much less than being present in both lists versus one. Worked numbers:

| chunk | keyword rank | semantic rank | fused score |
|---|---|---|---|
| A: "Coverage criteria … Emgality … two of the following" | 1 | 2 | 1/61 + 1/62 = **0.0325** |
| B: "Reauthorization … positive clinical response" | 7 | 1 | 1/67 + 1/61 = **0.0313** |
| C: "Emgality … quantity limits" | 2 | — | 1/62 = 0.0161 |
| D: "State-specific … California, Connecticut" | — | 4 | 1/64 = 0.0156 |

A and B win by a wide margin because both searches found them; C and D are close to each other despite one being a keyword-only hit and the other a semantic-only hit. That is the whole point of RRF: agreement between two different signals is worth more than a strong score from one. The top 8 by fused score become passages [1]–[8] in the context packet, in that order, and the number is what the model cites.

If you ever want keyword matches to count more than semantic ones (or vice versa) the formula takes a weight per list; we have not needed one.

## 4. The verifier backstop (word overlap)

After the draft comes back, `code_checks` runs before any judge model. For each claim it takes the set of content words in the claim and asks what fraction of them appear in the cited passage:

```
overlap = |claim words ∩ passage words| / |claim words|
```

Ben's first live run produced claims scoring 0.20–0.23 that were correct paraphrases ("your doctor needs to show the medicine is helping" against "positive clinical response"). The original threshold was 0.25 and the answer abstained three times. The lesson, now in DECISIONS.md, is that overlap cannot judge paraphrase, so the threshold is a floor of 0.08: it catches a claim that cites the wrong passage entirely (overlap near zero) and passes everything else to the judge. Overlap is a smoke detector, not a jury.

## 5. The judge (LLM-as-judge)

The judge is a haiku call that sees each claim beside its cited passages and returns SUPPORTED or NOT_SUPPORTED with a reason. It is the only step that understands meaning, and it is the actual decision for citation support. In the class comparison it also answers a second question, "does the passage attach this requirement to *this* medicine," which is how WRONG_DRUG rows get withheld.

## How to say it

- "Retrieval is hybrid: BM25 over FTS5 plus cosine over MiniLM embeddings, fused with reciprocal rank fusion at k=60, top 8 into the packet."
- "The drug name is injected into both the keyword query and the embedding query so retrieval is anchored to the right medicine."
- "Citation checking is two-stage: a lexical floor in code, then an LLM judge for paraphrase support."
- "Semantic similarity is drug-blind, so anything fanning out over shared documents needs a per-row attribution check."

## What to tune, and what happens

| Knob | Default | Turn it up | Turn it down |
|---|---|---|---|
| `top_k` | 8 | more context, more tokens, more chances to cite something marginal | tighter packet, more abstentions |
| `candidates` | 30 | deeper lists into fusion; rarely matters at this corpus size | faster; may miss a chunk one list ranked 31st |
| `rrf_k` | 60 | ranks matter less; presence in both lists matters more | the #1 of each list dominates |
| `LEXICAL_HARD_FLOOR` | 0.08 | more claims fail before the judge (cheaper, but re-creates the paraphrase problem) | judge sees everything, including obvious mis-cites |
