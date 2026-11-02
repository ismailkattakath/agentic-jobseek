# ADR-0007 — Evidence review at the point of use: one schema, three states, one sync

**Status:** accepted, 2026-11-02. Supersedes ADR-0006 § 1.3 (a separate weekly review) and its
whole-file apply. ADR-0006's discovery routine stays; what it produces, and when the operator
sees it, changes.

## 1. Problem

A weekly proposal file waits for a review nobody remembers to do. Three defects stop the
proposals from being used anywhere else:

1. `evidence-apply.py --write` moves the **whole** proposal file to `inbox/applied/`, so groups
   the operator never approved vanish with it.
2. Pending proposals are **not searchable**: Neon holds approved documents only.
3. There is **no rejected state**, so a declined item would be asked about again every session.

The naming audit (§ 6) found two more: the store has **two schemas** (475 documents carry a
frozen ragdb `id` integer and timestamps, 40 carry neither), and **`id` means two things** (that
integer in the file, an unrelated `bigserial` in Neon). Identity is `content_hash`, which changes
on every enrichment — the only reason `evidence-apply.py` remaps qrels.

## 2. Decision

1. **One document schema, three state files.** `evidence/approved.jsonl` (today's
   `career_docs.jsonl`), `evidence/pending.jsonl`, `evidence/rejected.jsonl`, all validated by
   one JSON Schema, `schemas/evidence-doc.schema.json`. A proposal *is* a document, plus
   `metadata.proposal` (§ 3).
2. **Identity is `doc_id`, version is `content_hash`.** Every document gets a stable `doc_id`
   (UUIDv7, RFC 9562 — `uuid.uuid7()`, Python ≥ 3.14). `content_hash` stays
   `sha256(content)` and names one *version*. qrels and every cross-reference key on `doc_id`,
   so enriching a document no longer moves its judgments. Precedent: a Git path versus its blob
   id; an HTTP URI versus its ETag.
3. **One sync job, `scripts/evidence-sync.sh`** (was `evidence-rebuild.sh`). Idempotent and
   level-triggered: it (a) takes in new proposal files from the inbox (§ 3), (b) enforces the
   invariant (§ 4), (c) mirrors all three files into the Neon table `evidence` with a `status`
   column (`approved` · `pending` · `rejected`), embedding only versions it has not embedded,
   and (d) records each file's sha256 in `evidence_sync` so drift is detectable. Running it
   twice changes nothing. **Primary key `content_hash`** (one row per version); `doc_id` is
   indexed, **not unique** — a pending enrichment shares its base's `doc_id`.
4. **Review happens where the evidence is needed: tailoring.** Retrieval always includes
   `pending` rows (same single SQL, `status` filter). The tailor asks the operator **only when a
   pending row would change the résumé** — it backs a requirement no approved row backs, or it
   outranks the best approved row for that requirement. Hits are **grouped by `doc_id`**: when a
   pending version shares a `doc_id` with an approved one (an enrichment), the approved version
   is the base and the question shows the **delta** — "<title> has a pending update: +N
   sentences" — never "new document found". One AskUserQuestion per item, four options:
   *Include (Recommended)* · *Not for this job* (stays pending) · *Reject* · *Edit first*.
5. **Approval is a move, never a copy.** `scripts/evidence-review.py approve|reject <doc_id>…`
   (replaces `evidence-apply.py`) moves the pending version out of `pending.jsonl`, then runs
   `evidence-sync.sh`. Nothing reaches `approved.jsonl` without the operator's choice.
   - *approve, new document* → appended to `approved.jsonl`.
   - *approve, enrichment* → **replaces** the approved row with the same `doc_id`; the old
     version's `content_hash` leaves every state file, and sync deletes its Neon row.
   - *reject* (either kind) → appended to `rejected.jsonl`; an approved base stays untouched.
6. **The routine dedupes against all three states.** Its match sees `approved`, `pending` and
   `rejected`, so new work on a pending item becomes an enrichment of that item, and rejected
   work is not proposed again.
7. **Monitoring foundation** (§ 5): a dead man's switch for the routine, a SessionStart digest
   for the backlog, drift detection in every search. OpenTelemetry is a later proof, not a
   dependency.

## 3. Contracts

**Routine output — unchanged in shape.** The routine still writes
`inbox/proposal-<stamp>.jsonl` and `inbox/run-<stamp>.json` (it can only *create* files through
the Drive connector, so it never edits a state file). Two field renames: `target_hash` →
`target_doc_id`, and `neighbours[]` carry `doc_id`, `content_hash`, `title`, `cosine`,
`status`.

**Intake (inside `evidence-sync.sh`)**, for each run file in `inbox/` whose proposal exists:

1. Refuse the proposal if its fingerprint differs from the run file's `proposal_sha256`
   (ADR-0006 § 4d); move both to `inbox/superseded/` with the reason.
2. `skip` → dropped (the run file keeps the count).
3. `new` → a pending document with a fresh `doc_id`.
4. `enrich` whose `target_doc_id` is approved → a pending document with the **same** `doc_id`
   (a new version of that document), the target's content plus the proposal's sentences.
5. `enrich` whose target is already pending → merged into that pending version in place
   (nothing approved changes, so no approval is needed yet).
6. Move the proposal and run file to `inbox/processed/` (was `applied/`).

Every pending document carries
`metadata.proposal = {kind, stamp, group, sources, read, numbers, removed_in_verify}`. Approval
moves `metadata.proposal` into `metadata.enrichments[]` (the audit trail), then removes it.

## 4. Invariant — the self-healing rule

> **Each `content_hash` lives in exactly one state file, and each `doc_id` appears at most once
> per file.**

`evidence-sync.sh` checks it on every run and repairs what a crash can leave behind: the same
version in two files → keep the most-decided copy (`approved` > `rejected` > `pending`) and
delete the other. A pending version whose `doc_id` is approved with the same `content_hash` is
already applied and is deleted.

Approval writes the target file **first**, then the source. A crash between the two leaves a
duplicate, which the next sync repairs. Each write re-reads the file's sha256 just before
`os.replace` and aborts if it changed (optimistic concurrency, the `If-Match` pattern), so two
sessions cannot silently overwrite each other. Every write backs up to `evidence/backups/` first.

**Neon is a cache.** Losing it costs one `evidence-sync.sh`. `evidence.sh` compares the local
files' sha256 with `evidence_sync`; on drift it runs the sync before searching.

## 5. Monitoring

| Signal | Mechanism |
|---|---|
| Routine **did not run** | Dead man's switch: after writing its run file, the routine pings `$JOBSEEK_HEARTBEAT_URL` (`/fail` when `errors` is non-empty). No ping inside the grace period → alert. Same shape as nix-config's `local.serviceHeartbeat`. |
| Routine ran **with errors** | Already sends a mobile push (ADR-0006). |
| Per-run trace | The run file in Drive, plus the routine's own session log. |
| **Backlog** | SessionStart digest (`scripts/evidence-digest.sh`, local files only): pending count, oldest pending age, last run's `until_at`. |
| Cache **drift** | `evidence.sh` (§ 4). |
| Cross-run trends | Later: Claude Code's native OTLP export from the cloud environment to a free Grafana Cloud stack, auth header injected by a network secret. Proven before adopted. |

## 6. Names

| Was | Now | Why |
|---|---|---|
| `evidence/career_docs.jsonl`, table `career_docs` | `evidence/{approved,pending,rejected}.jsonl`, table `evidence` + `status` | the concept is "evidence" everywhere else; the file name now *is* the state |
| `id` (frozen ragdb integer, 475 of 515 documents) | `doc_id` (UUIDv7, all documents) | one identity, one meaning |
| prose schema in `add-evidence` | `schemas/evidence-doc.schema.json` | 40 documents had drifted from it |
| `evidence-rebuild.sh` | `evidence-sync.sh` | it is incremental, not a rebuild |
| `evidence-apply.py` | `evidence-review.py` | it decides, per document |
| routine / environment / procedure `evidence-refresh` | `evidence-discover` | its job is discovery; "refresh" read as a cache refresh |
| `inbox/applied/` | `inbox/processed/` | in a job-search repo "applied" means a job application |
| table `resume_claims` | `claims` | matches `claims.jsonl` and `claims.sh` |
| `EVIDENCE_GITHUB_AUTHOR`, `NEON_PROJECT_ID`, `NEON_SQL_HOST` | `JOBSEEK_GITHUB_AUTHOR`, `JOBSEEK_NEON_PROJECT_ID`, `JOBSEEK_NEON_SQL_HOST` | one prefix per application; our values no longer pose as vendor variables |

Kept: `qrels`/`queries` (TREC), `resume.json` (JSON Resume), `FASTEMBED_CACHE_PATH`
(fastembed's own variable), `inbox/superseded/`, `neondb` (Neon's default).

## 7. Evidence

- **Neon headroom (measured 2026-11-02):** database 20 MB (`career_docs` 12 MB, 515 documents
  ≈ 23 KB each with indexes) against the Free plan's 1 GB per project, no deletion on
  inactivity (neon.com/pricing). Worst case ~20 proposals a week adds ~24 MB a year, so vectors
  for pending and rejected rows cost nothing that matters, and keyword-only search was rejected.
- **Schema drift (measured):** 475 documents with `id`/`created_at`/`updated_at`, 40 without.
- **Patterns:** state-by-location (Maildir), idempotent consumer (Enterprise Integration
  Patterns), level-triggered reconciliation (Kubernetes controllers), optimistic concurrency
  (HTTP `If-Match`).

## 8. Rejected

- **One file per proposal, state by directory (Maildir).** Atomic moves, but a second schema,
  a second validator and a second dedupe path. The invariant (§ 4) buys the atomicity back.
- **A `status` field inside one JSONL.** One file, but "move, never copy" becomes an edit in
  place, and a damaged line can no longer be told apart by which file holds it.
- **State in Neon.** The database is a cache (ADR-0002); state there would be a second truth.
- **Keyword-only search for pending rows.** Saves nothing measurable (§ 7) and splits ranking
  into two code paths.
- **Asking about every pending row in the top-k.** Questionnaire fatigue for rows that would
  not change the document.
- **Self-hosting an OTLP collector on nixpi.** Constant writes to a counterfeit SD card and a
  public ingress, for one run a week.

## 9. Migration

1. Back up both JSONL files and qrels; mint `doc_id` for every document; backfill
   `created_at`/`updated_at` for the 40 (earliest backup containing the hash, else the migration
   time) and drop the old integer `id`; rewrite qrels to `doc_id`; write
   `approved.jsonl`, empty `pending.jsonl` and `rejected.jsonl`. `career_docs.jsonl` moves to
   `evidence/backups/`, not the bin.
   The migration is a dry run first; it prints the counts (documents, `doc_id`s minted,
   documents backfilled, qrels rows rewritten) and writes only with `--write`.
2. Key `retrieval-eval.py` on `doc_id` **before** measuring. Create `evidence` and
   `evidence_sync` in Neon by copying `career_docs` joined to the `doc_id` map on
   `content_hash` — **vectors are copied, nothing is re-embedded**. Grant `SELECT` to
   `evidence_reader`. Parity is **exact**: the same row count and the same hybrid nDCG@10
   (0.722); any difference is a bug, not noise. Rename `resume_claims` → `claims`.
   `career_docs` is dropped only after the operator confirms.
3. Cloud side, in one sitting: rename the environment variables, the routine and the
   environment; update the routine's stored prompt, which names the procedure file; add the
   heartbeat host to the allowlist; push the repository last, so the Monday run never sees
   half a migration.
4. nix-config: the global `CLAUDE.md` line naming `career_docs`.
