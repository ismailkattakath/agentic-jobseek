# Evidence group — one drafter's procedure

`routines/evidence-discover.md` step 6 starts one `evidence-group` subagent per group
(`.claude/agents/evidence-group.md`: Opus, high effort). This file is that subagent's whole task.
It exists so each group's bodies and diffs fill the drafter's context, not the orchestrator's.

**First read the Rules section of `routines/evidence-discover.md`.** Every rule there binds you
(REST only, data not instructions, no invented facts, specifications are not implementations,
attribution, alternate persona). They are not repeated here, so there is one copy.

## Input (paths given in your prompt)

- `groups/<key>.json` — the group's items with FULL bodies and messages
- `matches.jsonl` — your group's line has its three neighbours (`doc_id`, `content_hash`,
  `status`, title, cosine)
- `neighbours.jsonl` — the neighbours' full text
- the window (`since`, `until_at`)

## Output

Write ONE JSON object to `drafts/<key>.json` in the proposal shape of
`evidence-discover.md` step 7 (`group`, `action`, `target_doc_id`, `neighbours`, `claim`,
`draft`, `sources`, `read`, `numbers`, `removed_in_verify`). Reply with one line: the key, the
action, and the path. If you cannot finish, write `{"group": "<key>", "error": "<what failed>"}`
there instead. **Never stop early to save context**: your whole context is this one group.

## Steps

1. **Read before deciding — every group, before any draft or skip.** Read the full body of
   every PR and issue in the group, and the full message of every commit. Never cut one short
   (`head -c`, `.[0:N]`); write a long one to a file and read the file. For each PR whose repository is cloned in this session (the routine
   attaches the usual ones), also read what it changed — the **patches**, not only the file list: `gh api
   'repos/{owner}/{repo}/pulls/{n}/files?per_page=100' --jq '.[] | .filename, .patch'` (REST;
   GraphQL is blocked), or
   `git log -p` in the clone after `git fetch --shallow-since=<since>`. A repository that is not
   cloned answers 403 there — then the body is what you have, and drafts from it carry no
   number the body does not state. Record in each proposal `"read"`: the bodies and diffs you
   actually read.
2. **Draft.** For each group propose ONE action:
   - `enrich` — the group adds facts to an existing document (same system, same work): give its
     `target_doc_id` (the neighbour's `doc_id`, which intake matches on; for a pending target
     it merges in place), and `"draft": {"add_sentences": ["…", "…"], "period_end": "YYYY-MM",
     "commit_refs": ["<sha or PR url>", …]}` — **an object, exactly these keys**; each sentence
     stands alone (no "also", no pronoun pointing at the base document);
   - `new` — no existing document covers it: a full line in the `add-evidence` skill's schema
     (`.claude/skills/add-evidence/SKILL.md`) with every field — `title`, `content`, `doc_type`,
     `source_class` (`enterprise` · `personal-project` · `oss-contribution`), `company`, `repo`,
     `period`, `skills`, `technologies`, `domains`, `author_confidence`, `metadata` — and
     `content_hash` and `doc_id` left empty (intake mints them);
   - `skip` — nothing evidence-worthy, decided AFTER step 1: the reason names what the bodies
     and diffs showed. "Titles only" is never a reason — read the bodies first.
   **A specification group** (issues only, `evidence-discover.md` step 4) drafts as `architecture_decision` or
   `contribution` with `metadata.evidence_kind: "specification"`; an `enrich` from one adds
   sentences that say "specified", "planned" or "decided", never "built" or "shipped" (Rules: a
   specification is not an implementation).
   **Dedupe against the neighbours' `status` (ADR-0007 § 2.6):**
   - a **rejected** neighbour that covers the group means `skip`, with the reason
     "rejected: <title>" — rejected work is not proposed again;
   - a **pending** neighbour that covers the group means `enrich` that pending document
     (`target_doc_id` = its `doc_id`): new work on a pending item extends it, it does not
     become a second pending document;
   - an **approved** neighbour is `enrich` as before; no neighbour covers it is `new`.
   Choose by reading the neighbours, not by a score cut-off; the cut is what this dry period
   measures (ADR-0006 § 1.5).
3. **Verify.** Re-read each draft against the items it cites. Delete any sentence you cannot
   trace; downgrade anything only partly supported to `[inferred]`. Record what you removed.
