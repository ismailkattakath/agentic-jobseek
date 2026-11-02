#!/usr/bin/env bash
# evidence.sh — retrieve corroborating evidence from the career_docs RAG for a claim,
# requirement, or bullet. This is the "evidence gate" primitive: tailoring and cover
# letters may only sharpen/add a point that this returns real backing for.
#
# The store (career_docs) is a pgvector cache in Neon of the operator's git-attributed career
# (docs/adr/0006), embedded with nomic-embed-text (768-d). The query is embedded on the client by
# scripts/embed.py and ranked by scripts/sql/evidence-search.sql — the same file the cloud
# evidence-refresh routine runs. The connection comes from scripts/db-env.sh (Keychain, never
# exported).
#
# Usage:
#   scripts/evidence.sh "<claim or JD requirement>" [--scope enterprise|personal-project|oss-contribution]
#                       [--types t1,t2,...] [--k N] [--full]
#   --scope   restrict to one source_class (default: all tiers)
#   --types   comma list of doc_type (default: the evidence-bearing types)
#   --k       number of rows (default 6)
#   --full    include the doc content (for drafting), not just the title line
#
# Examples:
#   scripts/evidence.sh "exactly-once processing on SQS" --scope enterprise
#   scripts/evidence.sh "diffusion / ComfyUI pipelines" --k 4 --full
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=db-env.sh
. "$here/db-env.sh" "$@"

usage() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-2}"
}

query=""
scope=""
k=6
full=0
types="contribution,hard_problem,skill_evidence,architecture_decision,unique_strength,subsystem,cross_repo_theme"
while [ "$#" -gt 0 ]; do
  case "$1" in
  --scope)
    scope="${2:-}"
    shift 2
    ;;
  --types)
    types="${2:-}"
    shift 2
    ;;
  --k)
    k="${2:-6}"
    shift 2
    ;;
  --full)
    full=1
    shift
    ;;
  -h | --help) usage 0 ;;
  -*)
    echo "unknown flag: $1" >&2
    usage
    ;;
  *)
    if [ -z "$query" ]; then query="$1"; else
      echo "unexpected arg: $1" >&2
      usage
    fi
    shift
    ;;
  esac
done
[ -n "$query" ] || {
  echo "error: a query string is required" >&2
  usage
}
case "$k" in '' | *[!0-9]*)
  echo "error: --k must be an integer" >&2
  exit 2
  ;;
esac

command -v psql >/dev/null 2>&1 || {
  echo "error: psql not found on PATH" >&2
  exit 1
}

# Stale-cache gate: the table is derived from the Drive JSONL (docs/adr/0002). A row count
# that differs means an edit to the source has not been rebuilt — refuse rather than answer
# from a cache that no longer matches its source.
src="${JOBSEEK_DRIVE_DIR:?set JOBSEEK_DRIVE_DIR to the job-search folder in Drive}/evidence/career_docs.jsonl"
[ -f "$src" ] || {
  echo "error: no evidence/career_docs.jsonl under JOBSEEK_DRIVE_DIR" >&2
  exit 1
}
want=$(grep -c . "$src" || true)
have=$(psql "$JOBSEEK_DB_URI" -X -tA -c "SELECT count(*) FROM public.career_docs" 2>/dev/null || echo "missing")
if [ "$have" != "$want" ]; then
  echo "error: career_docs has $have rows but the source has $want — run scripts/evidence-rebuild.sh" >&2
  exit 1
fi

# Sanitize the doc_type CSV into a Postgres text-array literal (letters/underscores only).
types_arr=$(printf '%s' "$types" | tr ',' '\n' | sed 's/[^a-z_]//g' | sed '/^$/d' | paste -sd, -)
[ -n "$types_arr" ] || {
  echo "error: --types produced no valid doc_type" >&2
  exit 2
}

cols="d.doc_type, '[' || d.source_class || '] ' || d.title, d.company, d.repo"
[ "$full" -eq 1 ] && cols="$cols, d.content"
qv=$(python3 "$here/embed.py" query "$query")
psql "$JOBSEEK_DB_URI" -X -q -v ON_ERROR_STOP=1 -tA -F $'\t' -v qv="$qv" -v q="$query" \
  -v scope="$scope" -v types="{$types_arr}" -v k="$k" -v cols="$cols" -f "$here/sql/evidence-search.sql"
