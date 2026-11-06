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
  script wrote `advisorModel: "opus"` to the user settings (Sonnet 5.5 accepts Opus 5+ as
  advisor). It failed silently (§ 4g) and was removed; the run file's `advisor` field stays, so
  a future route shows up as soon as it works.

| Rejected | Why it lost |
|---|---|
| Smaller windows / daily cron | volume still decides whether a run fits; the early stop was not about volume |
| A per-run group cap with resume state | adds state to the procedure for a limit the run never hit |
| Opus for the whole session | 2× the cost on orchestration that needs no judgment |

## 4g. First drafter run, 2026-11-06 — what worked, what broke

Re-run of 2026-08-01..08-11 on the § 4f design (session `cse_01G12ck8RqJXDTJQS11RQP1b`):

| Check | Result |
|---|---|
| Visibility after the Claude GitHub App was installed on the missing org | ✅ 97 PRs and 65 issues: equal to a local count of the same window |
| Drafters | ✅ `drafter: evidence-group` — the setup script's symlink loaded the Opus agent; **28 of 28** groups drafted (15 enrich, 11 new, 2 skip) in ~30 min |
| Advisor via `advisorModel` in user settings | ❌ `advisor: none` — not offered to a cloud session this way; the setup-script line was removed the same day (dead config) |
| Proposal upload | ❌ 317,686 bytes could not go through one Drive connector call verbatim; nothing reached intake |

**Fix tried — the proposal travels in parts (superseded by § 4h the same day).** The routine splits it into ≤ 40,000-byte parts
(50,716 bytes had uploaded intact on 2026-11-06), records each part's content fingerprint in the
run file's `proposal_parts`, and uploads the run file last. Intake waits for every part, checks
each fingerprint, and takes the parts in as one all-or-nothing run. Tested on a copy of the
store: the same 19 outcomes from two parts as from one file; a changed part → `superseded`; a
missing part → not taken in yet.

| Rejected | Why it lost |
|---|---|
| Lower `max_groups` per run | splits the work across more runs instead of fixing the transport; a busy week would still fail |
| Slimmer proposal lines (drop `neighbours`, `read`) | they are the audit trail intake and review rely on |
| Uploading from a script | the sandbox holds no Drive credential; the connector is the only path |

**Cost, measured:** one drafter used ~131K tokens (`tk-agent-spec`), so a 28-group window is on
the order of 3–4M Opus tokens.

## 4h. The proposal reaches Drive through an Apps Script drop, 2026-11-06

§ 4g's parts did not fix the transport; they spread the same failure across more calls. The
next run drafted all 30 groups, then uploaded only 6 of 30 parts. The Drive connector makes the
model **retype every byte** (285 KB here): one part came out with placeholder text, one was cut
from 5,331 to 4,221 bytes, and a safety classifier stopped the response mid-upload. No run file
was written. Retyping is the root cause; § 4d's fingerprint only detected it.

**Decision:** a script uploads, never the model. `apps-script/inbox-drop/` is a ~70-line Apps
Script web app, deployed as the operator, `drive.file` scope only. It writes a POSTed
`{token, name, content}` into its own `inbox/drop/` folder and answers with the sha256 of what
it wrote. `scripts/inbox-upload.py` posts the proposal, then the run file, and fails on any
hash mismatch. Intake scans `inbox/drop/` beside `inbox/`. The parts machinery was reverted:
one byte-exact file needs no splitting.

| Check, from this Mac against the deployed drop | Result |
|---|---|
| 315,040-byte JSONL with Unicode, quotes, tabs | ✅ drop's sha256 = local sha256; the Drive-synced copy hashes the same |
| same name again | ✅ refused `exists` |
| name outside `proposal-*.jsonl` / `run-*.json` | ✅ refused `bad name` |
| wrong token | ✅ refused `unauthorized` |
| after moving `drop/` into `evidence/inbox/` | ✅ uploads land there (drive.file follows the folder) |

- **The token travels in the JSON body**, never the URL (proxies and request logs keep URLs;
  flagged by review). Apps Script cannot read request headers, so a network secret cannot carry
  it; it sits in the environment variables beside the heartbeat URL — the same risk class: it
  can only add files to the drop, and intake still checks the fingerprint and keeps everything
  pending.
- **Precedent:** jsonresume-connect is the operator's Apps Script + clasp project with the same
  `drive.file` + advanced Drive v3 pattern.

| Rejected | Why it lost |
|---|---|
| A Neon inbox table | the operator's Neon quota is limited; Drive stays the one home (§ 2) |
| Drive API with an OAuth refresh token | a long-lived, broader Google credential in an environment variable |
| rclone + service account | service accounts hold no My Drive storage, so the inbox would move to a Shared Drive |
| Slimmer proposals / fewer groups | the model still retypes; the failure becomes rarer, not impossible |

### 4h.1 First clean run and its three follow-ups, 2026-11-06

The Aug 1–11 window then ran end to end: 27 drafts (11 new, 16 enrich), uploaded byte-exact
(fingerprint and byte count equal on the Drive copy), taken in as pending, all embedded, in
**20 min**. Seven of 27 drafters reported 74K–194K tokens (mean ~108K), so a 10-day window is on
the order of **3M drafter tokens** (estimated from that sample). Hybrid nDCG@10 stayed at
**0.711**. Follow-ups:

- **GitLab drafts came from titles alone** — subagents do not see the routine's connector. Step 3
  now fetches each commit's full message (and diff) with `get_commit` into the group file.
- **Alternate-persona marker** — 45 approved and 3 pending documents from the alternate GitLab
  account's projects lacked `metadata.persona: "alt"`, identified by `repo` (and by gitlab.com
  evidence URLs) after the operator named which projects are that account's; mixed-repo
  documents are marked too (the conservative choice). Metadata only: no content hash changed,
  no re-embedding.
- **Repositories attached** for diffs: the two busiest repositories of the operator's own
  venture org, per the § 4e attach rule.
- **Embedding** — merged pending documents outgrew Ollama's 2,048-token window (HTTP 500);
  `scripts/embed.py` moved to `/api/embed` with `truncate`, matching the cloud's fastembed
  `max_length=2048` (cosine 1.000000 on stored rows).

## 4i. Deterministic grouping — `scripts/group.py`, 2026-11-06

A group is the unit of the whole pipeline: one group → one match → one Opus drafter (~100K
tokens) → one proposal line → at most one evidence document. Until now the orchestrator grouped
by judgment, writing a throwaway script each run: the same window (2026-09-30..10-10) came out
as **92 groups** on one pass and **40** on the next, the second with 40- and 33-item groups that
fold several citable pieces of work into one blurred document.

**Signals, measured on that window's 286 merged PRs:** 87 carry a conventional-commit scope
and 35 a nix-config area prefix; **118 (41 %) have no title scope** (that repository titles PRs
with bare areas such as `claude, docs`); 75 close an issue; no single scope exceeds 13 PRs.

**Decision:** `scripts/group.py` groups; the model only writes each group's match sentence and
may merge two groups that are plainly one feature (recorded as `merges`). The subsystem key is,
in order: the commit **scope** (it names the feature), an issue's epic tag, the **changed
paths** (three levels, docs/tests/agent-memory excluded), the title's area, else the item
itself. Items union on closing links (`Closes #N`, a squash subject's `(#N)`) and on the same
(repository, subsystem); a group over 15 splits by subsystem, then ISO week; one- and two-item
fragments pool by area, then per repository. `coverage.json` accounts for every item read.

| Iteration on the real window | Groups | Largest | Defect found |
|---|---|---|---|
| paths at depth 2, shared `spec` key for unscoped issues | 97 | 15 | one chained component (every issue + every PR closing one); week splits of unrelated work |
| issues key on themselves, paths at depth 3 | 212 | 15 | structural fragments (`backend-makefile`, `docs-operations-finops` apart from `finops`) |
| **scope first, fragments pooled** | **99** | 15 | none in a spot check: `dns` (12, all ADR-018), `finops`, `hosts/nixvm` coherent |

Deterministic: two runs give identical groups (diffed). Coverage: 519 grouped + 13 chores + 66
copies + 364 PR-merge commits = 962 read. A fabricated fixture (`scripts/test/group-fixture.json`)
runs in the offline gate: identical output twice, closing links join, unscoped issues do not
chain, chores/copies/merges counted, no group over 15. `max_groups` now defaults to 100.

| Rejected | Why it lost |
|---|---|
| Model judgment with grain rules (`8c9fe00`) | still not reproducible; a coverage gap is only seen after the fact |
| Title scope only | leaves 41 % of PRs unkeyed |
| Embedding clustering | adds a model, a threshold to tune, and still needs a size cap |

## 4j. A scripted pipeline around one judgment step, 2026-11-06

Nine cloud runs today took **16–36 min** per window, with throughput varying 2× for similar
group counts. The model's own filtering differed run to run (one window reported 90 direct
commits where `group.py` keeps 156 from the same data). Logs showed drafters re-reading the two
procedure files 2–4 times each, the orchestrator reading group bodies and hand-building neighbour
files (one wrote column names as the values, one compared a uuid with text), drafters re-querying
Neon, output reshaped by hand, and stamps handled ad hoc. **8 %** of the day's 262 proposal lines
fail the line schema. The operator set the priority: **determinism first**, draft quality fixed.

**Decision — scripts around the drafting:**

| Stage | Who | What |
|---|---|---|
| `prepare.py --phase group` | script | paged GitHub discovery split under the cap (reproduced 286 / 156 / 520 in 37 s), `group.py`, `titles.txt` |
| match sentences | **model**, fixed template | see the measurement below |
| `prepare.py --phase match` | script | `match.py`, neighbours via `scripts/sql/neighbours.sql`, one brief per group (identical across runs) |
| `.claude/workflows/evidence-draft.js` | workflow | one `evidence-group` drafter per brief, 5 lanes; each drafter self-validates with `assemble.py --check` |
| `assemble.py` | script | validates every line against `schemas/proposal-line.schema.json`, builds proposal + run file, coverage, stamp rule |

The drafter reads `routines/evidence-group.md` (now holding the drafting rules) and its brief
once each. The repo's built-in validator agrees with `jsonschema` on 266 of 266 real lines, so
one engine runs everywhere.

**Cloud smoke test** (one-off routine, same environment): the Workflow tool runs a script by
`scriptPath` from the attached repo; 8 of 8 schema-valid returns; agents write files. The
runtime caps concurrency at `min(16, CPUs − 2)`, which was **2** here, until
`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS=5` was set on the environment: then **5**, and 8 ×
20 s tasks took 58 s instead of 122 s.

**Match sentence — measured before switching:** over 120 enrich groups from today's windows,
the drafter's chosen target was in the top 3 for **95.8 %** of the stored (model-sentence)
matches, **76.7 %** for a scripted title sentence and **82.5 %** for a richer one (up to 8 item
descriptions). The bar was within 5 points, so the model keeps this one step, from a fixed
template. The baseline is biased upward (drafters chose targets from the model's neighbours),
but the scripted sentence never found a target the model missed (0 vs 23).

**Found on the way:** the hybrid search failed outright on a query holding a URL. Its tsquery
joined raw lexemes such as `github.com/x)`, which is not tsquery syntax. Lexemes are now
`quote_literal()`'d. That is quality-neutral: hybrid nDCG@10 is 0.707 with old and new SQL on
today's store; the drift from 0.711 is the 112 pending documents added today.

| Rejected | Why it lost |
|---|---|
| User-settings hooks (`~/.claude/settings.json`) | cloud sessions do not read user settings |
| Repo `.claude/settings.json` hooks or env | a multi-repo session reads only `enabledPlugins` |
| TaskCreate / TodoWrite checklists | bookkeeping, not enforcement; a fixed script order is stronger |
| Nested `claude -p --json-schema` drafters | cloud auth and billing undocumented; `--bare` bills as API |
| Workflow `schema` holding the full proposal shape | scripts cannot read files, so it would be a second copy of the schema; drafters run `--check` against the one file |
| Scripted match sentences | 82.5 % vs 95.8 % (above) |

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
