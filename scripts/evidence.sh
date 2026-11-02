#!/usr/bin/env bash
# evidence.sh — retrieve corroborating evidence from the career_docs RAG for a claim,
# requirement, or bullet. This is the "evidence gate" primitive: tailoring and cover
# letters may only sharpen/add a point that this returns real backing for.
#
# The store (career_docs) is a local pgvector table of the operator's git-attributed career,
# embedded on-device (nomic-embed-text, 768-d). Retrieval embeds the query IN-DB via the
# embed() function, so no client-side embedding is needed — one SQL statement.
#
# Requires RAGDB_URI in the environment (the ragdb connection string; a loopback,
# trust-auth pgvector store — not a secret, but kept out of the repo so no infra
# coordinates are committed). Equivalent path: the global `postgres` MCP `execute_sql`
# with the same query. See CLAUDE.md > Evidence backend.
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

: "${RAGDB_URI:?set RAGDB_URI to the ragdb connection string (loopback pgvector career store)}"
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
have=$(psql "$RAGDB_URI" -X -tA -c "SELECT count(*) FROM career_docs" 2>/dev/null || echo "missing")
if [ "$have" != "$want" ]; then
  echo "error: career_docs has $have rows but the source has $want — run scripts/evidence-rebuild.sh" >&2
  exit 1
fi

# Sanitize the doc_type CSV into a safe SQL array literal (letters/underscores only).
types_sql=$(printf '%s' "$types" | tr ',' '\n' |
  sed 's/[^a-z_]//g' | sed '/^$/d' | sed "s/.*/'&'/" | paste -sd, -)
[ -n "$types_sql" ] || {
  echo "error: --types produced no valid doc_type" >&2
  exit 2
}

cols="doc_type, '['||source_class||'] '||title AS title, company, repo"
[ "$full" -eq 1 ] && cols="$cols, content"

# Query text + scope bind as psql vars (:'q' / :'scope' quote safely). embed() runs in-DB.
# NOTE: psql interpolates :'var' only from stdin/files, NOT from -c — so feed the SQL via stdin.
# The query vector is computed ONCE in a materialized CTE: written inline in ORDER BY, the
# planner seq-scans and calls embed() — an HTTP call to Ollama — once per ROW (measured
# 2026-11-01: 8.9 s per query over 477 rows).
# HYBRID: reciprocal-rank fusion (k=60) of the top 50 by vector distance and the top 50 by
# full-text rank (any query word, English stemming). Measured on the graded set
# (docs/adr/0004): nDCG@10 +0.104 over vector-only, 95% CI [+0.051, +0.159].
sql="WITH q AS MATERIALIZED (SELECT embed('search_query: ' || :'q')::vector AS v),
     tq AS MATERIALIZED (SELECT to_tsquery('english', coalesce(nullif(array_to_string(
            tsvector_to_array(to_tsvector('english', :'q')), ' | '), ''), 'xxnomatchxx')) AS t),
     pool AS (SELECT * FROM career_docs
              WHERE (:'scope' = '' OR source_class = :'scope') AND doc_type = ANY(ARRAY[$types_sql])),
     v AS (SELECT p.id, row_number() OVER (ORDER BY p.embedding <=> q.v) AS r
           FROM pool p, q ORDER BY p.embedding <=> q.v LIMIT 50),
     b AS (SELECT p.id, row_number() OVER (ORDER BY ts_rank_cd(p.tsv, tq.t) DESC) AS r
           FROM pool p, tq WHERE p.tsv @@ tq.t ORDER BY ts_rank_cd(p.tsv, tq.t) DESC LIMIT 50),
     f AS (SELECT id, sum(1.0 / (60 + r)) AS s FROM (SELECT * FROM v UNION ALL SELECT * FROM b) u GROUP BY id)
     SELECT $cols
     FROM f JOIN career_docs USING (id)
     ORDER BY f.s DESC
     LIMIT :k;"
printf '%s\n' "$sql" | psql "$RAGDB_URI" -X -q -v ON_ERROR_STOP=1 -tA -F $'\t' \
  -v q="$query" -v scope="$scope" -v k="$k"
