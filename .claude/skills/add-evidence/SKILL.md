---
name: add-evidence
description: >
  Add, correct or retire career evidence — the documents the evidence gate checks résumé
  claims against. Edits the ONE source of truth, $JOBSEEK_DRIVE_DIR/evidence/approved.jsonl,
  through the evidence store, then syncs the pgvector cache. Use when asked to "add evidence",
  "record this project", "mine <repo> for evidence", "the store is missing X", "correct an
  evidence doc", or after a new role, repo or shipped system that future applications should be
  able to cite.
---

# Add evidence

The three JSONL files in Drive (`evidence/{approved,pending,rejected}.jsonl`) are the source;
the `evidence` table is a cache that `scripts/evidence-sync.sh` mirrors from them (ADR-0007).
**Never write to the table directly** — a row that exists only in the cache is lost on the
next sync. Documents the operator adds by hand go straight to `approved.jsonl`; documents the
weekly routine discovers arrive in `pending.jsonl` and are approved at tailoring time
(`scripts/evidence-review.py`).

## The schema

**`schemas/evidence-doc.schema.json` is THE schema.** `scripts/evidence-schema.py` validates
against it, and `scripts/evidence-store.py` refuses to write a row that fails. The table below
is a summary for reading; where it and the schema disagree, the schema wins.

One line per document:

```json
{"doc_id": "<UUIDv7>", "title": "…", "content": "…", "content_hash": "<sha256 of content>",
 "doc_type": "contribution", "source_class": "enterprise",
 "company": "…", "repo": "…", "period": "YYYY-MM..YYYY-MM",
 "skills": ["…"], "technologies": ["…"], "domains": ["…"],
 "author_confidence": "mixed",
 "metadata": {"evidence": ["what in the repo shows it"], "commit_refs": ["abc1234"]},
 "created_at": "<ISO 8601 UTC>", "updated_at": "<ISO 8601 UTC>"}
```

| Field | Values |
|---|---|
| `doc_id` | stable identity, UUIDv7 (RFC 9562), minted by the store; survives enrichment. `content_hash` names one *version* |
| `source_class` | `enterprise` · `personal-project` · `oss-contribution` — the tier the gate uses to keep personal work from licensing an enterprise metric |
| `doc_type` | `contribution` · `hard_problem` · `skill_evidence` · `architecture_decision` · `unique_strength` · `subsystem` · `cross_repo_theme` · `repo_overview` · `resume_corroboration` |
| `author_confidence` | the operator's handle (sole author) · `mixed` · `teammate` · `unknown` — how much of the work is provably the operator's |
| `period` | `YYYY` · `YYYY..YYYY` · `YYYY-MM..YYYY-MM` · `multi-year` |
| `metadata.commit_refs` | when the evidence is code: the commits that prove it, **attributed to the operator** (check `git log --author`) |

## Steps

1. **Mine from the source**, never from memory: read the repo (`git log --author=<operator>`,
   the code, the PRs) and write what it shows. Every claim gets an entry in
   `metadata.evidence`.
2. **Write through the store, never by hand-editing the file.** `scripts/evidence-store.py`
   (load it by path; the name is hyphenated) is the only writer: `store.load("approved")`
   returns `(rows, sha)`, and `store.write("approved", rows, sha, reason="add-evidence")`
   validates every row against the schema, backs up to `evidence/backups/`, and refuses
   (`Conflict`) if another session changed the file meanwhile. A new document gets a fresh
   `doc_id` (`uuid.uuid7()`, Python 3.14+) and `created_at`/`updated_at` from `store.now_iso()`.
   To correct a doc, replace its row (same `doc_id`, new `content_hash`). To retire one,
   move it: `scripts/evidence-store.py move <doc_id> approved rejected`.
3. **Hash:** `content_hash` = `sha256(content)` in hex (`store.normalize` fills it). The
   store rejects a mismatch.
4. **Sync:** `scripts/evidence-sync.sh` — it embeds only new or changed versions, is
   idempotent, and repairs the one-file-per-version invariant.
5. **Check:** `scripts/evidence.sh "<the new claim>" --k 3` returns the new doc.
6. **Duplicates:** `scripts/evidence-dups.sh` — a new doc that restates an existing one (same PR,
   same fix) is merged into the more precise doc (fold in its unique facts, record
   `metadata.merged_from`), then sync. An overview and a detail doc are not duplicates.
7. **After a large batch,** re-score retrieval: `scripts/retrieval-eval.py build && scripts/retrieval-eval.py score`
   (docs/adr/0004). New documents are unjudged until the pool is re-graded.
