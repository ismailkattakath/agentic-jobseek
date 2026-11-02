---
name: backfill-applications
description: >
  Re-ground existing applications' résumés and cover letters on the evidence store by
  re-running the evidence-gated tailor over each role folder. Use when asked to "backfill
  applications", "re-ground every application", "re-tailor everything after the evidence
  store changed", or after evidence was added that older applications never saw.
argument-hint: "[company/role ...] [--scope enterprise|personal-project|oss-contribution] [--dry-run]"
---

# Backfill applications

**Arguments**
- None → every folder under `$JOBSEEK_DRIVE_DIR/applications/`.
- `company/role` slugs → only those (e.g. `acme-corp/staff-engineer`).
- `--scope <tier>` → evidence scope for the whole run. Default: per role — `enterprise` for
  principal/staff narratives, all tiers for founding / applied-AI / generalist roles.
- `--dry-run` → report what would change per folder; write nothing.

## Preconditions — check once, STOP on failure

1. `scripts/evidence.sh "smoke test" --k 1` returns a row. It also proves the cache matches
   its Drive source (drift triggers a sync first). If not, **stop and say how to fix it** — never fall back to the master
   résumé (`rules/evidence-gate.md`).
2. `jsonresume validate --url "$JOBSEEK_MASTER_URL"` passes.

## For each folder, one at a time

1. `posting.md` present and non-empty? No → skip, note "no posting captured".
2. Run the `tailor-application` skill on it: evidence per requirement → tailor from the
   master → validate → render → regenerate `cover-letter.md` from retrieved
   `contribution` / `hard_problem` / `unique_strength` docs → append a `status.md` log line
   (`<today> — backfilled: evidence-grounded tailor`).
3. Record: bullets changed · keywords added (each with its backing title) · true-but-omitted
   strengths surfaced · **gaps** the store cannot back · the PDF path.

## Rules

- **Idempotent** — a folder already grounded should change little.
- **Send nothing.** Finish with a table (folder · bullets changed · keywords added · gaps ·
  PDF) and ask the operator to open each PDF before using it.
