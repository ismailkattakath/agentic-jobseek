# ADR-0006 — Periodic evidence refresh: a cloud routine proposes, a local step applies

**Status:** accepted, 2026-11-02. Decided on a cloud proof run and an embedding-parity test
(§ 4); not yet built. Next: a second proof run for what § 4b lists, then a one-week dry run
(§ 6).

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
| Propose | cloud | `evidence/inbox/<date>.jsonl` + `evidence/watermark.json` in Drive (connector) |
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
0.5 GB per project; a weekly run of minutes against 100 CU-hours a month; no inactivity pause.

## 4b. Not yet measured — the second proof run's checklist

The first proof run did not exercise the Match step's dependencies:

- **Neon from a cloud run.** `console.neon.tech` answered 401 over HTTP; untested are the Neon
  connector's SQL tool accepting a 768-float vector literal, and Postgres wire protocol (5432)
  from the sandbox — the allowlist covers HTTP hosts, and network secrets attach only to HTTP.
- **The model download.** `huggingface.co` is not on the default allowlist. The routine needs
  its own environment with that host allowed, and the model fetched in the **setup script**,
  whose result is cached per environment, so a run does not re-download about 550 MB.
- **The GitLab connector.** Which account it is signed in as, and whether it lists that
  account's merge requests.

## 5. Rejected

| Option | Why it lost |
|---|---|
| bragdoc CLI (MIT, 27★) | own cache and schedule, cloud sync by default, export format and author attribution undocumented, no GitLab — a second store |
| brag-skill, bragbot, Git Brag, brag-ai | 2–8★; produce documents, not records |
| awesome-copilot `brag-sheet` as the engine | prompt text, not a tool — its rules are adopted (§ 3), not the skill |
| Desktop local scheduled task (nix-config ADR-010, lane 1) | needs the Mac awake and the app open; GitHub, Drive and PyPI are proven reachable from the cloud (§ 4), the rest is § 4b |
| Attach every source repository | each is cloned on every run; REST search finds the work without it |
| Neon AI Gateway embeddings | not on the Free plan; a new model would void ADR-0004 |
| Supabase | Free projects pause after 7 days of low activity — the weekly cadence's edge |
| Local pgvector kept as the cache | the cloud run cannot reach it, so matching could not happen before the proposal |
| SemDeDup-style clustering | built for millions of documents; at 514, hash + cosine pairs suffice |

## 6. Weakest assumption

That grouping by subsystem gives documents at the right grain. **Settle it** with a one-week
dry run: count proposed *new* against *enrich*. A week holds ≥ 204 merged PRs; if most become
new documents, the grain is wrong.

Also open: the commit author name used in client repositories — `git log --author` matched
only 2 of a week's commits in one shallow clone, so discovery keys on the PR author handle,
not commit metadata.
