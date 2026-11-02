# ADR-0002 — Data in Drive, evidence as a rebuildable cache

**Status:** accepted, 2026-11-01. Superseded in part by ADR-0006: the cache moves to Neon and the
in-database `embed()` goes away; the JSONL stays the source.

## Decision

1. **Every fact lives in Google Drive**, under the folder `$JOBSEEK_DRIVE_DIR` points at. The
   repo holds procedures only (`rules/no-data-here.md`). No Drive path is written here: the
   mount path carries the account's email address.
2. **The evidence store's source of truth is `evidence/career_docs.jsonl` in Drive.** The
   `career_docs` pgvector table is a **derived cache**, rebuilt by
   `scripts/evidence-rebuild.sh`. It holds no fact of its own.

## Why the RAG is a cache, not a store

A retrieval index is a card catalogue: burn it and you re-catalogue an afternoon, you lose no
book. That holds only if the books exist somewhere else — so the text goes to Drive and the
vectors are recomputed from it.

| Property | Evidence |
|---|---|
| The vectors are reproducible from the text | Re-embedding `content` alone with `embed()` gave cosine distance **0** against the stored vector (3 of 3 rows sampled, 2026-11-01). Since ADR-0004 the input is `'search_document: ' || content`, recorded per row in `embed_spec` |
| A rebuild is faithful | Rebuilding all **477** rows into a scratch table: 0 missing, metadata identical, max cosine distance **4.4e-8** (float rounding) |
| A rebuild is cheap when nothing changed | 23 s for a cold rebuild; **0.17 s** for a re-run, which re-embeds nothing (`content_hash` = sha256 of `content`) |

**What breaks the cache's faithfulness:** a different embedding model. `embed()` names
`nomic-embed-text`; if the local Ollama pulls a different build under that name, recomputed
vectors drift from any held elsewhere. Rebuilding the whole table is the fix, and it is
cheap.

## Guarding the cache against its source

- `evidence.sh` **refuses to answer** when the table's row count differs from the JSONL's:
  an edit to the source that was never rebuilt.
- `evidence-rebuild.sh` validates every line first — parseable, `content_hash` equal to
  sha256(`content`), no duplicates — then makes the table a mirror: insert new, refresh
  metadata in place, delete what left the file.
- New evidence is written **to the JSONL**, never to the table (`add-evidence` skill).

## Rejected

| Option | Why it lost |
|---|---|
| Keep the pgvector table as the only copy | The program that mined it no longer exists; the table was the sole copy of 477 documents on one machine |
| Vertex AI RAG Engine over the Drive folder | Metered, replaces a working local store, and a forum report says a missing folder permission imports nothing **without an error** |
| Drive full-text search only | Keyword match; loses the paraphrase recall the gate depends on |
| A Google Sheet as the evidence source | Long `content` cells and nested `metadata` fit JSONL; a Sheet adds a cell-size ceiling for no reader who needs it |

## Weakest assumption

Drive for Desktop serves the JSONL and role folders reliably to scripts. A file that is
cloud-only blocks on first read until it downloads; scripts wait rather than fail. Not yet
measured under a cold cache.
