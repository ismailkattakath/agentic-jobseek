---
name: add-evidence
description: >
  Add, correct or retire career evidence — the documents the evidence gate checks résumé
  claims against. Edits the ONE source of truth, $JOBSEEK_DRIVE_DIR/evidence/career_docs.jsonl,
  then rebuilds the pgvector cache. Use when asked to "add evidence", "record this project",
  "mine <repo> for evidence", "the store is missing X", "correct an evidence doc", or after a
  new role, repo or shipped system that future applications should be able to cite.
---

# Add evidence

The JSONL in Drive is the source; the `career_docs` table is rebuilt from it. **Never write
to the table directly** — a row that exists only in the cache is lost on the next rebuild.

## One line per document

```json
{"title": "…", "content": "…", "content_hash": "<sha256 of content>",
 "doc_type": "contribution", "source_class": "enterprise",
 "company": "…", "repo": "…", "period": "YYYY-MM..YYYY-MM",
 "skills": ["…"], "technologies": ["…"], "domains": ["…"],
 "author_confidence": "mixed",
 "metadata": {"evidence": ["what in the repo shows it"], "commit_refs": ["abc1234"]}}
```

| Field | Values |
|---|---|
| `source_class` | `enterprise` · `personal-project` · `oss-contribution` — the tier the gate uses to keep personal work from licensing an enterprise metric |
| `doc_type` | `contribution` · `hard_problem` · `skill_evidence` · `architecture_decision` · `unique_strength` · `subsystem` · `cross_repo_theme` · `repo_overview` · `resume_corroboration` |
| `author_confidence` | the operator's handle (sole author) · `mixed` · `teammate` · `unknown` — how much of the work is provably the operator's |
| `period` | `YYYY` · `YYYY..YYYY` · `YYYY-MM..YYYY-MM` · `multi-year` |
| `metadata.commit_refs` | when the evidence is code: the commits that prove it, **attributed to the operator** (check `git log --author`) |

## Steps

1. **Mine from the source**, never from memory: read the repo (`git log --author=<operator>`,
   the code, the PRs) and write what it shows. Every claim gets an entry in
   `metadata.evidence`.
2. **Append** the line to the JSONL. To correct a doc, replace its line. To retire one,
   delete its line.
3. **Hash:** `content_hash` = `sha256(content)` in hex. The rebuild rejects a mismatch.
4. **Rebuild:** `scripts/evidence-rebuild.sh` — it embeds only new or changed content.
5. **Check:** `scripts/evidence.sh "<the new claim>" --k 3` returns the new doc.
