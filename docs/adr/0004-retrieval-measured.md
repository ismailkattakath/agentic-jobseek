# ADR-0004 — Retrieval, measured: hybrid search, task prefixes, near-duplicate review

**Status:** accepted, 2026-11-01. Every change here was decided on the graded test set below,
not on a recommendation.

## 1. The test set — `scripts/retrieval-eval.py`

- **Queries** (`$JOBSEEK_DRIVE_DIR/evidence/eval/queries.jsonl`, 55): what a tailor actually
  asks — one application's `job.json` requirements (26), the claims ledger (24), and hard
  requirements from earlier postings (5). Eleven malformed posting queries were dropped (an
  extraction regex read a section header as a requirement).
- **Judgments** (`qrels.jsonl`, 631): TREC-style pooling — the union of every system's top 5
  per query — graded blind (shuffled, no system or rank shown) by four LLM assessors on a
  0/1/2 rubric (2 = proves the claim, 1 = related, 0 = not), in the manner of UMBRELA-style
  LLM relevance assessment. Distribution: 96 × 2, 273 × 1, 262 × 0. **22 of 55 queries have no
  grade-2 document** — the store honestly lacks proof for them (DynamoDB, Node on Lambda,
  regulated-industry), which is the evidence gate working, not a retrieval miss.

## 2. Results (nDCG@10, 55 queries)

| System | nDCG@10 | Recall@10 | Success@3 | MRR |
|---|---|---|---|---|
| vector, raw text (before) | 0.599 | 0.639 | 0.327 | 0.793 |
| vector, nomic task prefixes | 0.608 | 0.645 | 0.345 | 0.834 |
| BM25 (Postgres full text) | 0.621 | 0.607 | 0.473 | 0.802 |
| **hybrid: RRF(prefixed vector, BM25)** | **0.703** | **0.719** | 0.455 | **0.885** |

Paired bootstrap over queries (5,000 resamples):

| Comparison | Mean Δ nDCG@10 | 95% CI | Wins / losses / ties |
|---|---|---|---|
| prefixes vs raw | +0.009 | [−0.025, +0.043] | 30 / 21 / 4 |
| **hybrid vs raw** | **+0.104** | **[+0.051, +0.159]** | 39 / 14 / 2 |
| hybrid vs prefixes | +0.095 | [+0.047, +0.143] | 43 / 9 / 3 |

## 3. Decisions

- **Hybrid search is production** (`scripts/evidence.sh`): reciprocal-rank fusion (k = 60) of
  the top 50 by vector distance and the top 50 by full-text rank. The keyword half is a
  generated `tsvector` column over title + content with a GIN index. 0.15 s per query.
  *Precedent:* Anthropic's Contextual Retrieval measured embeddings + BM25 cutting retrieval
  failures 49% vs embeddings alone; RRF is the standard rank-fusion method.
- **Task prefixes adopted** (`search_document:` / `search_query:`). Not a measurable gain here
  (CI spans zero) — adopted because the model card says the prefix *"must"* be present, and it
  did not hurt. Applied in our SQL, **not** in the shared `embed()` function, which also embeds
  another table. Each row records its recipe in `embed_spec`; a rebuild re-embeds any row made
  under another recipe.
- **Near-duplicate review** (`scripts/evidence-dups.sh`, threshold 0.94): two genuine
  duplicates found and merged (the same work written twice, from two mining runs). Exact
  duplicates were already impossible (`content_hash` unique). The report decides nothing; an
  overview and a detail doc about one system are not duplicates.

## 4. Not adopted (yet), and what would change that

| Option | Why not now | Revisit when |
|---|---|---|
| Contextual chunk prefixes (Anthropic) | Our docs are whole, self-describing records, not fragments of a larger document; "prefixes + title" did not beat prefixes alone on the 22-claim pilot | docs ever get chunked |
| A reranker (cross-encoder or LLM) | Needs another model and per-query latency; hybrid already lifted MRR to 0.885 | Success@3 matters more than it does now (the tailor reads `evidence.md`, not one hit) |
| Whole store in the prompt (no RAG) | ~210k tokens — just over Anthropic's 200k guideline, and every tailor would pay for it | the store shrinks, or prompt caching makes it cheap enough to measure |
| ESCO / O*NET skill IDs on documents | Normalising `skills` to a taxonomy helps cross-posting matching, which this repo does not do | discovery starts ranking postings against the store |
| Open Badges 3.0 as the evidence format | See §5 | a verifier outside this repo needs to consume the evidence |

## 5. Open Badges 3.0 (1EdTech, Final Release, 2026-06-29) as the evidence format

Its `AchievementCredential` is a W3C Verifiable Credential: an `Achievement` (what was done,
with `alignment[]` to frameworks — `targetType` `ceasn:Competency`, `targetFramework`,
`targetCode`), an `AchievementSubject` (who, `activityStartDate`/`EndDate`, `role`), and
`evidence[]` (`narrative` Markdown, `name`, `genre`, `id` URI; only `type` required).

| Advantage | Disadvantage |
|---|---|
| A real, versioned standard with JSON Schemas and a JSON-LD context | It is a **credential exchange** format; our records are private retrieval documents — most of the model (issuer, proof, credential status, endorsement) has nothing to fill |
| `evidence[].narrative` + `id` map cleanly onto `content` + commit/PR URLs | **Verifiability needs a signing issuer.** A self-issued credential (issuer = subject) proves nothing a plain record does not; the value of OB3 is a third party's signature |
| `alignment[]` gives skills a standard home (ESCO/O*NET codes) | Our needs are not in it: `source_class` (enterprise/personal/OSS tier the evidence gate depends on), `author_confidence` (teammate attribution), `doc_type`, `content_hash` — they would become `ext:` extensions anyway |
| Exportable to wallets, LinkedIn-style displays, a Comprehensive Learner Record | Verbose JSON-LD for 514 records whose only reader is a retrieval script |

**Decision:** keep the JSONL record as the source of truth. If an outside consumer ever
needs it, write an **exporter** (record → `AchievementCredential` with `evidence[]` and
`alignment[]`), not a migration — the same "derived artifact" rule as the pgvector cache.
