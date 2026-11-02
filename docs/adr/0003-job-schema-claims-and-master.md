# ADR-0003 — Job schema, claims ledger, one master, and the theme

**Status:** accepted, 2026-11-01. Evaluated four proposals; three adopted, one rejected.

## 1. Postings become `job.json` in the JSON Resume job-description schema — ADOPTED

- **What:** `schemas/job-schema.json`, vendored from `jsonresume/jsonresume.org` at commit
  `88ae28b4060af3bfab9188e5fb6c59f60c883b46` (`packages/schema/job-schema.json`). Draft-07,
  13 fields; `responsibilities[]`, `qualifications[]` and `skills[]` are the ones that matter.
- **Precedent:** jsonresume.org itself GPT-extracts job posts into this schema (`gpt_content`
  in its `jobs` table) and matches résumés to them by embedding (`match_jobs_v5`) — see that
  repo's `apps/docs/content/docs/020-jobs-subsystem.md`.
- **Why:** it turns "check the posting against the store" into a checklist —
  `scripts/job-evidence.sh` runs one query per requirement. On the first application it ran,
  the hand-picked query list had missed SQS/EventBridge; per-item queries cannot.
- **Searched first:** no skill, plugin or MCP server emits this schema. The hits were résumé
  parsers (NovaCV `convert_resume_text`, jsonify-resume, Resume Parser MCP) and tailoring tools
  (tailor-resume, job-hunter). The global `job-description-analyzer` skill extracts the same
  facts as prose. So extraction is a Claude step, validated by `jsonschema`.
- **Beat:** schema.org `JobPosting` (`posting.json`), retired. Two structured copies of one
  posting would drift; `extract-posting.py` now maps a page's JSON-LD *into* `job.json`.
- **Vendored, not fetched:** the offline `project-gate` must validate without a network, and a
  pinned commit cannot change under the gate. Re-vendor deliberately, with the new SHA here.

## 2. A claims ledger, not "start from the closest past résumé" — ADOPTED (reshaped)

- **Proposal as given:** seed each tailor from the most similar earlier `resume.json`.
- **Rejected as given:** earlier tailors inherit the master's errors. The fact-check that
  motivated this ADR found teammate-owned work ("AI usage metering") **in the master**, and every
  earlier tailor was copied from that master. Reusing whole résumés re-spreads it.
- **Adopted instead:** `evidence/claims.jsonl` in Drive — every ruling (`backed`, `narrowed`,
  `teammate`, `gap`, `stale`, `unverifiable`) with the wording to use and its backing titles —
  cached in `resume_claims` like `career_docs`. Reuse is per **vetted bullet**, and a rejected
  claim is caught before the fact-checker has to find it again. Seeded with 24 rulings.
- **Measured:** a draft "built DynamoDB tables…" returns the earlier `teammate` ruling at
  similarity 0.81 in 0.13 s.
- **Not built:** embedding `job.json` to find the nearest past posting (jsonresume.org's
  pattern). At 16 applications the nearest one is found by eye. Revisit past ~100.

## 3. The master résumé has exactly one copy — ADOPTED

- The master is the published JSON Resume at `$JOBSEEK_MASTER_URL` (the operator's public gist,
  which the email signature, the `jsonresume` CLI default and the jsonresume.org registry also
  read). Drive held a stale second copy (v1.5.0 vs the gist's v1.5.1); it was deleted.
- Corrections reach it through `scripts/master-patch.py`: a reviewed JSON-Pointer patch with
  exact-match assertions, a version bump and a `meta.notes` entry, dry-run by default and
  published only with `--publish` after the operator approves (final pass, Step E). v1.5.2 was
  the first such correction, made by hand on 2026-11-01.
- `new-application.sh` fetches the master per application. GitHub's raw CDN caches for a few
  minutes after an edit — a tailor started seconds after a master fix can get the old version.

## 4. Reactive Resume — REJECTED; theme trial instead

- **Reactive Resume** (MIT, ★45k, v6.0.9 2026-10-29): its own schema — its docs say *"not the
  community JSON Resume format"* — imports JSON Resume lossily (dates rewritten, skills lose
  `description`), and needs a server and database. It would be a second source of truth with
  no evidence gate.
- **Theme trial** (the actual pain: 5 pages, no "Present" on the current role), one tailored
  résumé, 2026-11-01:

| Theme | Pages | Current role | `final-check.py` | Problem |
|---|---|---|---|---|
| academic-cv-lite (current) | 5 | `Mar 2026` (no Present) | 0 fail | length |
| elegant | 4 | `Mar, 2026 - Present` | 0 fail | icon font missing: contact icons render as empty boxes |
| macchiato | 3 | `03/2026 - Present` | 1 fail (skills heading) | two-column: sidebar text interleaves with dates in the text layer |
| onepage-plus | 3 | `Feb 2026` | 1 fail | **dates off by one month** (timezone) — disqualified |
| stackoverflow | 5 | — | 2 fail | no Experience/Education headings in the text layer |
| kendall | 6 | — | — | longer |
| even, professional | — | — | — | PDF export fails |

- **Decision: stay on academic-cv-lite** — the only theme with a clean text layer and no
  defect. Length is cut in content, not by theme.
- **Superseded 2026-11-02 by ADR-0005:** the PDF now renders with the repo's own Typst theme
  (`theme/`); this trial is the record of why no HTML theme was kept.
- `final-check.py` also learned the date formats these themes print (`Mar, 2026`, `03/2026`) —
  it had failed elegant and macchiato on dates that were present.
