# agentic-jobseek

A Claude Code project for a job search: **discover** postings, **tailor** a JSON Resume to each
role — only with claims an evidence store backs — and **track** every application.

**This repo holds procedures, never data.** Résumés, postings, applications and career evidence
live in Google Drive, reached through `$JOBSEEK_DRIVE_DIR`.

## Setup

```bash
echo 'export JOBSEEK_DRIVE_DIR="<your job-search folder in Drive for Desktop>"' > .envrc.local
direnv allow                       # .envrc sources the gitignored .envrc.local
secret set neon:agentic-jobseek:uri  # the cache's Postgres+pgvector connection string (docs/adr/0006)
echo 'export JOBSEEK_DB_SECRET="neon:agentic-jobseek:uri"' >> .envrc.local
scripts/evidence-sync.sh           # sync the evidence cache from $JOBSEEK_DRIVE_DIR/evidence/
```

## Use

```bash
scripts/new-application.sh "Acme Corp" "Staff Engineer"   # scaffold a role folder in Drive
# paste the job description into its posting.md, then in Claude Code:
#   "tailor the Acme Corp staff-engineer application"     → the tailor-application skill
scripts/render.sh "$JOBSEEK_DRIVE_DIR/applications/acme-corp/staff-engineer" "Staff Engineer"
scripts/final-check.py "$JOBSEEK_DRIVE_DIR/applications/acme-corp/staff-engineer"
```

See `CLAUDE.md` for the layout and the rules, and `docs/adr/` for the decisions.

## Needs

`nix develop` (or `direnv allow`) supplies jq, psql, poppler, Python 3 and shellcheck.
Bring your own `jsonresume` (over `resumed`). Optional: `jobspy`.
