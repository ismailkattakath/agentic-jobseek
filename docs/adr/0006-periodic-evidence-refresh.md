# ADR-0006 — Periodic evidence refresh: a cloud routine proposes, a local step applies

**Status:** accepted, 2026-11-02. Decided on a cloud proof run and an embedding-parity test
(§ 4); not yet built. Both proof runs passed (§ 4, § 4b). Next: a one-week dry run (§ 6).
**Superseded in part by ADR-0007:** § 1.3 (the separate weekly review) and the whole-file `evidence-apply.py` are replaced by review at the point of use (`evidence-review.py`); `career_docs` → `evidence`, `evidence-rebuild` → `evidence-sync`, `evidence-refresh` → `evidence-discover`, `inbox/applied/` → `inbox/processed/`, `target_hash` → `target_doc_id`, and the env vars gain the `JOBSEEK_` prefix. Names below are historical.

## 1. Decision

1. **Extend `add-evidence`; adopt no brag tool.** Every tool surveyed keeps its own store of
   achievements, which would be a second copy of the evidence (§ 5).
2. **A weekly cloud scheduled task (routine) mines and proposes.** It finds the operator's
   merged work since a watermark, groups it by subsystem, matches each group against the store,
   drafts *enrich* or *new* lines, fact-checks them against the repositories, and writes a
   **proposal** to Drive. It never writes `career_docs.jsonl`.
3. **A local step applies.** The operator reviews the proposal; the apply step merges approved
   lines into the JSONL (`metadata.merged_from` / `superseded` as today) and rebuilds the cache.
   Dry run → approve → apply, the same shape as `master-patch.py`.
4. **The vector cache moves to Neon (Free plan); embeddings stay nomic-embed-text, computed by
   the client.** Local scripts embed with Ollama; the cloud run embeds with `fastembed`
   (`nomic-ai/nomic-embed-text-v1.5`). **Neon holds text and vectors**: hybrid search needs the
   full-text half (ADR-0004's `tsvector` over title + content), so the evidence text sits in
   Neon as well as in Drive. It stays a derived cache — rebuilt from the JSONL, holding no fact
   of its own — but it is a copy of the text outside Google, accepted for this job. The local
   pgvector table retires, so there is still one cache. ADR-0002 is superseded in part: the
   in-database `embed()` over pgsql-http to a local Ollama goes away; scripts send vectors.
5. **Enrich is the default, a new doc the exception.** A group whose nearest document is
   close enough becomes an update to that document: extended `period`, appended `commit_refs`,
   new facts, a fresh `content_hash`. **The cut is an output of the dry run, not an input.**
   `evidence-dups.sh`'s 0.94 measured doc against near-duplicate doc; a week's new facts about
   a known subsystem will score lower, so borrowing it would route most enrichments to *new*.
   The dry run records each group's nearest-doc score and the operator's enrich/new verdict,
   and the cut is chosen from those pairs.

## 2. Pipeline

| Step | Where | Mechanism |
|---|---|---|
| Discover | cloud | REST `search/issues?q=author:<handle>+is:pr+is:merged+merged:>=<watermark>` across all repos; GitLab through the claude.ai GitLab connector |
| Group | cloud | by repo + subsystem — "ten commits on one feature = one entry" |
| Match | cloud | pre-filter by `repo` (free, structural), then `fastembed` query vector → Neon top-k, hybrid RRF as in ADR-0004 |
| Draft | cloud | `add-evidence` schema; every line tagged `[measured]` / `[source]` / `[inferred]` |
| Verify | cloud | adversarial check of each draft against the attached repository |
| Propose | cloud | `evidence/inbox/<date>.jsonl` + `evidence/watermark.json` in Drive (connector; superseded by `until_at` in `run-<stamp>.json`, ADR-0007 § 3) |
| Apply | local, operator present | approve → JSONL → `evidence-rebuild.sh` → `retrieval-eval.py score` after a large batch |

**Watermark, not "last 7 days":** a run can be late or skipped; the next run starts from the
last processed merge date per source.

## 3. Rules the routine follows

- **Sources:** GitHub through the session's one credential, the primary account; the GitHub
  proxy substitutes that account and no other. The alternate persona through the claude.ai
  GitLab connector. A second GitHub account would need its own path (§ 4b). Evidence from
  the alternate persona carries `metadata.persona: "alt"`; tailoring never prints that handle,
  its repository names or its URLs in anything public.
- **Attribution:** commits an agent executed under the operator's direction are the
  operator's work — `author_confidence` is the operator's handle, with
  `metadata.agent_assisted: true` recorded, never a downgrade.
- **No invented metric.** A number enters only from a record (precedent: awesome-copilot
  `brag-sheet`, "never invent a metric to fill the gap").
- **REST only.** GraphQL is blocked in cloud sessions (§ 4).

## 4. Evidence

**Cloud proof run, 2026-11-02** (one-off routine, report only, Default environment, 3 repos
attached, one of them a 414 MB private client repository):

| Check | Result |
|---|---|
| Clone of a private client-org repository | ✅ cloned in 36 s |
| Clone depth | shallow (50 commits); `git fetch --shallow-since` deepened it (50 → 932 on another repo) |
| REST `search/issues` across all repositories | ✅ `total_count` 204 merged PRs in one week |
| REST on an attached repository | ✅ |
| REST on a repository **not** attached | ❌ 403 "not enabled for this session" |
| `gh search prs`, `gh pr list` (GraphQL) | ❌ 403 "GitHub GraphQL is not available from Claude Code sessions" |
| Google Drive connector | ✅ found the 1,555,420-byte JSONL; created a file |
| `gitlab.com` unauthenticated | 429 — use the connector |
| PyPI | ✅ 200 |
| Tools | Python 3.11, psql 16, node 22, jq; no `glab`; 4 CPUs, 15 GB RAM |
| Wall time | ≈ 3.5 min (clone 40 s, setup 80 s, agent 78 s) |

**Embedding parity, 2026-11-02:** 25 stored vectors (the 5 longest documents + 20 random),
re-embedded with `fastembed` nomic-embed-text-v1.5 and the `search_document: ` prefix, against
the vectors Ollama's `nomic-embed-text` (F16, 768-d) produced: cosine **min 0.999999, mean
1.0, max 1.000001**. Ollama and the cloud run produce the same vectors, so ADR-0004's
measurements and the 0.94 threshold carry over without a re-embed.

**Neon Free fits:** text 1.56 MB + 514 × 768-d vectors ≈ 1.6 MB + index, under 10 MB against
0.5 GB per project (now 1 GB per project, neon.com/pricing); a weekly run of minutes against 100 CU-hours a month; no inactivity pause.

## 4b. Second proof run, 2026-11-02 — the Match step's dependencies

One-off routine in a dedicated environment (`evidence-refresh`: Limited network + package
managers + `huggingface.co`, `*.huggingface.co`, `*.hf.co`; a setup script that installs
`fastembed` in a venv and downloads nomic-embed-text-v1.5 to `/opt/fastembed-cache`):

| Check | Result |
|---|---|
| Setup script, first session | 2 min 56 s (523 MB model) — under the ~5 min cache limit, so it is snapshotted |
| Embedding from the cached model | 768-d, **7.7 s** cold, no download |
| Neon connector `run_sql` with a full 768-float literal (8,058 bytes) passed three times | ✅ `d = 0`, `n = 768`; pgvector 0.8.6 |
| GitHub REST search under Limited network | ✅ 206 |
| GitLab connector | ✅ signed in as the alternate persona; listing merge requests needs a project or group, and the persona's work is mostly direct pushes, so GitLab discovery reads **commits per project**, not merge requests |
| Scheduled `run_once_at` | ❌ **did not fire** — no session, no warning, `next_run_at` stuck past due; **Run now** worked |

**Consequences.** The snapshot expires after about 7 days, so a weekly run usually re-runs
setup (~3 min, free). A silently missed schedule is real: the routine appends one line per run
to `evidence/runs.jsonl` in Drive (date, sources read, proposals written; superseded by `run-<stamp>.json` with `until_at`, ADR-0007 § 3), and the watermark
makes a missed week a late week, not a lost one. "Succeeded" on the History row means only
that the session ended cleanly; the run log carries the truth.

## 4c. The cache switch to Neon, measured 2026-11-02

| Check | Result |
|---|---|
| Rows, vectors, metadata after the load | identical — one md5 over every `content_hash`, vector and `embed_spec` matched on both sides |
| `scripts/embed.py` (POST /api/embeddings, as the retired `embed()`) vs `embed()` | cosine 1.0 |
| Full rebuild from the JSONL into a scratch table, embedding on the client | 514 rows in 26 s, every vector cosine 1.0 against the stored ones |
| `claims.sh` search, old (local) vs new (Neon) | identical rows |
| **Hybrid search quality, 55 graded queries, the production SQL** | local **0.727** nDCG@10, Neon **0.722** (stale figure; see ADR-0007 § 10); Δ −0.0045, 95% CI [−0.0097, −0.0003], 5 wins / 9 losses / 41 ties |
| HNSW index vs exact scan | the same numbers on both sides — the index is not the cause |

**Why Neon ranks 0.0045 lower:** its keyword index tokenizes differently. Neon runs PostgreSQL 18
with a `C.UTF-8` database; the local store ran 16 with `C`. 439 of 514 `tsv` rows differ: the
newer Snowball stemmer keeps *added* as `add` (16 made it `ad`), and `—`, `→` and `…` stop being
indexed as words. Both are corrections; the cost is half a point on this set. Accepted.

**Two tools fixed on the way.** The judged pool was built from `retrieval-eval.py`'s own copy of
hybrid search, so 38 % of production's top-10 was unjudged; 215 pairs were graded blind (four
assessors, 20/24 hidden controls exact, 24/24 within one grade) and `qrels.jsonl` grew from 631 to
846 judgments. `retrieval-eval.py` now scores `scripts/sql/evidence-search.sql` — the query
tailoring runs — instead of re-implementing it: the old copy reported 0.703 while production was
0.727 (ADR-0004 corrected).

**Connections.** Scripts read the Keychain item named by `JOBSEEK_DB_SECRET` through
`secret exec` (`scripts/db-env.sh`), never an exported variable, and connect directly rather than
through Neon's pooler: the pooler's transaction mode drops temp tables between transactions and
carried a dump's `search_path` into the next client.

## 4d. Run-log review, 2026-11-02 — three fixes before the first real week

Re-reading every run log found two weaknesses the dry runs had not surfaced:

| Found | Evidence | Fix |
|---|---|---|
| The proposal reaching Drive is retyped by the model | dry run 3 computed a 28,920-byte proposal; the Drive copy was 23,913 bytes — every skip's neighbours dropped on the way | the run records `scripts/proposal-fingerprint.py` (a content hash) in its run file; `evidence-apply.py` refuses a proposal whose fingerprint differs (whitespace-tolerant, value-strict) |
| Groups skipped as "titles only" | 5 of 8 skip reasons said so, though the search had returned each PR body | step 6 reads every full PR body, and the diff of each PR in an attached repository, before any draft or skip; a skip names what was read |
| Numbers drafted from PR text alone | every run reported "bodies, not diffs" | every number in a draft is listed with its exact source (body, diff path, commit); a number without one is deleted. The routine now attaches the repositories most work lands in, so their diffs are readable over REST |

## 4e. Coverage, 2026-11-06 — commits and specifications join merged PRs

A coverage audit asked whether one client organisation's repositories reached the routine.
Measured locally with the same REST endpoints the cloud uses:

| Source | Measured | Before | Now |
|---|---|---|---|
| Merged PRs the operator authored, any base branch | 621 in that org since 2026-08-08; the search total matched a `pulls` listing to within 1 | ✅ | ✅ step 2a |
| Direct pushes (one parent, committer ≠ `GitHub`) | **all repositories, August 2026: 1,225 commits on default branches — 930 direct, 256 web-flow PR merges, 39 merge commits**; 11 of the 930 in that org | ❌ | ✅ step 2b |
| Issues the operator authored (specifications) | 478 in that org since 2026-08-08 | ❌ | ✅ step 2c, as `evidence_kind: specification` |
| Teammates' PRs the operator only reviewed or specified | 10 reviewed; ≥3 of 300 sampled teammate PRs close an operator issue | ❌ | ❌ by decision: a teammate's code never backs the operator's claim |

- **Copies are deduped by `author.date` + subject, not by repository flag.** A backup
  repository re-listed 172 of its source's 177 August commits; an archived repository shared
  0 with either and holds distinct work, so "skip archived" would have lost real evidence.
- **The watermark is the largest `until_at`, not the newest file.** A backfill run writes an
  older `until_at`; reading the newest file would rewind the next weekly run.
- **Volume:** August alone exceeds the search API's 1,000-result cap for commits, so step 2
  splits a window by week, then by day. A weekly run stays under it.
- **Weakest assumption:** that the cloud GitHub proxy serves `search/commits`; § 4 proved only
  `search/issues`. Step 2b records a 403 in the run file instead of failing the run, so the
  first run settles it.
- **Attach rule:** attach a repository once it holds the operator's merged work in the trailing
  90 days, so a drafter (routines/evidence-group.md) can read its patches; review at each amendment. "Attach everything" stays
  rejected (§ 5).

| Rejected | Why it lost |
|---|---|
| `repos/{o}/{r}/commits?author=` per repository | 403 for any repository not attached; needs a repository list in the procedure |
| `users/{u}/events` | 300 events and 90 days at most — under one month of this volume |
| An `org:` qualifier or owner allow-list | puts identifiers in the procedure, and the other repositories' direct pushes outnumber the org's 919 to 11 (August) |

## 4f. One drafter subagent per group, 2026-11-06

The first full-visibility backfill pass (2026-08-01..08-11: 97 PRs, 65 issues, 110 direct
commits) finished **5 of 13 groups** and recorded the rest as "INCOMPLETE RUN (context budget)".
Its own measurement says the budget was not the cause: every PR and issue body together was
212,427 characters (~53K tokens), the session ran 209 s over 23 turns with no autocompaction,
and Sonnet 5.x sessions run with a 1M-token window by default (code.claude.com model-config).
The model stopped early by choice.

- **Fix, two parts:** the orchestrator (Sonnet 5.5) discovers, groups and matches, then starts
  one `evidence-group` subagent per group (`.claude/agents/evidence-group.md`: `model: opus`,
  `effort: high`, both documented subagent frontmatter keys), which follows
  `routines/evidence-group.md`. And both procedures say "never stop early to save context".
  Volume now sets the number of drafters, not whether a run fits.
- **Why Opus drafts and Sonnet orchestrates:** drafting is where the evidence gate's judgment
  lives; orchestration is mechanical. Opus 5.5 lists at 2× Sonnet 5.5 per token
  (platform.claude.com pricing), paid only on the reading-heavy part.
- **Loading the agent:** a session with several repositories starts above the clones, and the
  docs only promise repo `.claude/agents/` loading in general terms. The environment's setup
  script symlinks the file into `~/.claude/agents/` (user scope, read at start; a symlink, so
  the cached snapshot never holds a stale copy). Fallback: `general-purpose` with
  `model: "opus"`, recorded in the run file's `drafter`.
- **Advisor (operator's choice to try):** the advisor tool is experimental and undocumented for
  routines, and a multi-repo session ignores repo settings except `enabledPlugins`. The setup
  script writes `advisorModel: "opus"` to the user settings, which Claude Code reads at start
  (Sonnet 5.5 accepts Opus 5+ as advisor; subagents inherit it). The run file's `advisor` field
  records whether the session was offered one; `none` means the attempt failed silently.

| Rejected | Why it lost |
|---|---|
| Smaller windows / daily cron | volume still decides whether a run fits; the early stop was not about volume |
| A per-run group cap with resume state | adds state to the procedure for a limit the run never hit |
| Opus for the whole session | 2× the cost on orchestration that needs no judgment |

## 5. Rejected

| Option | Why it lost |
|---|---|
| bragdoc CLI (MIT, 27★) | own cache and schedule, cloud sync by default, export format and author attribution undocumented, no GitLab — a second store |
| brag-skill, bragbot, Git Brag, brag-ai | 2–8★; produce documents, not records |
| awesome-copilot `brag-sheet` as the engine | prompt text, not a tool — its rules are adopted (§ 3), not the skill |
| Desktop local scheduled task (nix-config ADR-010, lane 1) | needs the Mac awake and the app open; every dependency is proven reachable from the cloud (§ 4, § 4b) |
| Attach every source repository | each is cloned on every run; REST search finds the work without it |
| Neon AI Gateway embeddings | not on the Free plan; a new model would void ADR-0004 |
| Supabase | Free projects pause after 7 days of low activity — the weekly cadence's edge |
| Local pgvector kept as the cache | the cloud run cannot reach it, so matching could not happen before the proposal |
| SemDeDup-style clustering | built for millions of documents; at 514, hash + cosine pairs suffice |

## 6. Dry runs, 2026-11-02 — the grain question settled

Three Run-now passes over 2026-10-26..11-02 (209 merged PRs, 42 GitLab commits):

| Run | What went wrong | Fix |
|---|---|---|
| 1 | Match skipped (17 KB statements), hashes cut to 10 characters, GitLab unauthorised | vector referenced once; full hashes required; connector re-authorised |
| 2 | 5 of 8 groups matched: every 7 KB vector was retyped by the model into a connector call | `scripts/match.py` — Bash embeds and queries Neon over HTTPS as a read-only role through a network secret; no vector passes through the model |
| 3 | none | 20 groups → **11 enrich, 1 new, 8 skip**; 7 chores and 15 persona-operations commits dropped |

**Grain: right.** 251 items became 12 changes, not 251 documents. The operator approved all 12
(one clause cut in review) and `scripts/evidence-apply.py` applied them: 514 → 515 documents,
11 enriched with their previous hashes recorded, 5 graded judgments carried to the new hashes.
**Enrich cut:** every enrich chose its top neighbour at cosine 0.68–0.82, and the one new
document's nearest neighbour scored 0.66 — a threshold near 0.67 would have agreed with the
reviewer on all 12, but twelve pairs are too few to fix it; the routine keeps choosing by reading
the neighbours, and the pairs accumulate in `inbox/applied/`.

**Still open:** drafts rest on PR titles and bodies, not diffs (superseded: § 4d reads patches); a scheduled `run_once_at` did not
fire once (the weekly cron is unproven until its first Monday).
