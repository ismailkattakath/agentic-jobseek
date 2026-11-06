#!/usr/bin/env bash
# evidence.sh — retrieve corroborating evidence from the evidence RAG for a claim,
# requirement, or bullet. This is the "evidence gate" primitive: tailoring and cover
# letters may only sharpen/add a point that this returns real backing for.
#
# The store (table `evidence`) is a pgvector cache in Neon of the operator's git-attributed career
# (docs/adr/0006, docs/adr/0007), embedded with nomic-embed-text (768-d). The query is embedded on
# the client by scripts/embed.py and ranked by scripts/sql/evidence-search.sql — the same file the
# cloud evidence-discover routine runs. The connection comes from scripts/db-env.sh (Keychain,
# never exported; psql gets it through PG* environment variables, never argv).
#
# Drift gate (ADR-0007 § 4): the sha256 of each local state file is compared with `evidence_sync`;
# on a difference — or when a discovery run waits in the inbox — this runs scripts/evidence-sync.sh
# first (one line on stderr says so), which takes the run in as pending.
#
# Usage:
#   scripts/evidence.sh "<claim or JD requirement>" [--scope enterprise|personal-project|oss-contribution]
#                       [--types t1,t2,...] [--k N] [--full] [--with-pending]
#   --scope         restrict to one source_class (default: all tiers)
#   --types         comma list of doc_type (default: the evidence-bearing types)
#   --k             number of rows (default 6)
#   --full          include the doc content (for drafting), not just the title line
#   --with-pending  also search pending (unreviewed) versions. Every row then starts with three
#                   extra tab-separated fields: status, doc_id, content_hash. A pending row's title
#                   is marked "PENDING" — it is a lead, NEVER evidence until approved — and, when an
#                   approved version shares its doc_id, "UPDATE of an approved document: +N sentences".
#
# Examples:
#   scripts/evidence.sh "exactly-once processing on SQS" --scope enterprise
#   scripts/evidence.sh "diffusion / ComfyUI pipelines" --k 4 --full --with-pending
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=db-env.sh
. "$here/db-env.sh" "$@"

usage() {
  sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-2}"
}

query=""
scope=""
k=6
full=0
pending=0
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
  --with-pending)
    pending=1
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

# Drift gate (ADR-0007 § 4): the table is derived from the three Drive state files. Their sha256 is
# recorded in `evidence_sync` by every sync; if it differs (or the table is missing) the cache is
# stale, so sync before answering rather than answer from a cache that no longer matches its source.
: "${JOBSEEK_DRIVE_DIR:?set JOBSEEK_DRIVE_DIR to the job-search folder in Drive}"
[ -f "$JOBSEEK_DRIVE_DIR/evidence/approved.jsonl" ] || {
  echo "error: no evidence/approved.jsonl under JOBSEEK_DRIVE_DIR" >&2
  exit 1
}
shas="$(python3 -I "$here/evidence-store.py" sha)"
sha_of() { printf '%s\n' "$shas" | awk -F'\t' -v s="$1" '$1 == s { print $2 }'; }
synced=$(
  psql -X -tA -v a="$(sha_of approved)" -v p="$(sha_of pending)" -v r="$(sha_of rejected)" <<'SQL' 2>/dev/null || echo 0
SELECT count(*) FROM public.evidence_sync
WHERE (file, sha256) IN (('approved.jsonl', :'a'), ('pending.jsonl', :'p'), ('rejected.jsonl', :'r'));
SQL
)
# Intake at the point of use: a finished run waiting in inbox/ or inbox/drop/ (the routine uploads
# its run file last) is taken in before answering, so tailoring sees it — on this Mac, never in the
# cloud: the routine only proposes (ADR-0006 § 1; ADR-0007 § 3).
waiting=$(find "$JOBSEEK_DRIVE_DIR/evidence/inbox" -maxdepth 2 -name 'run-*.json' \
  -not -path '*/processed/*' -not -path '*/superseded/*' 2>/dev/null | grep -c . || true)
if [ "$synced" != 3 ] || [ "$waiting" -gt 0 ]; then
  if [ "$waiting" -gt 0 ]; then
    echo "evidence.sh: $waiting discovery run(s) waiting in the inbox — running evidence-sync.sh first" >&2
  else
    echo "evidence.sh: the Neon cache differs from the Drive files — running evidence-sync.sh first" >&2
  fi
  log="$(mktemp)"
  "$here/evidence-sync.sh" >"$log" 2>&1 || {
    cat "$log" >&2
    rm -f "$log"
    echo "error: evidence-sync.sh failed; not answering from a stale cache" >&2
    exit 1
  }
  rm -f "$log"
fi

# Sanitize the doc_type CSV into a Postgres text-array literal (letters/underscores only).
types_arr=$(printf '%s' "$types" | tr ',' '\n' | sed 's/[^a-z_]//g' | sed '/^$/d' | paste -sd, -)
[ -n "$types_arr" ] || {
  echo "error: --types produced no valid doc_type" >&2
  exit 2
}

# The title cell: a pending version says so, and says what it is relative to the approved base.
# +N sentences = what the proposal added (metadata.proposal.added_sentences, plus every later merge).
title="'[' || d.source_class || '] ' || d.title"
statuses="{approved}"
cols="d.doc_type, $title, d.company, d.repo"
if [ "$pending" -eq 1 ]; then
  statuses="{approved,pending}"
  title="CASE WHEN d.status <> 'pending' THEN $title
       WHEN EXISTS (SELECT 1 FROM public.evidence a WHERE a.doc_id = d.doc_id AND a.status = 'approved')
         THEN 'PENDING (UPDATE of an approved document: +' || (
                coalesce(jsonb_array_length(d.metadata->'proposal'->'added_sentences'), 0)
              + coalesce((SELECT sum(jsonb_array_length(m->'added_sentences'))
                            FROM jsonb_array_elements(d.metadata->'proposal'->'merged') m), 0)
              ) || ' sentences) ' || $title
       ELSE 'PENDING (NEW document) ' || $title END"
  cols="d.status, d.doc_id, d.content_hash, d.doc_type, $title, d.company, d.repo"
fi
[ "$full" -eq 1 ] && cols="$cols, d.content"
qv=$(python3 "$here/embed.py" query "$query")
psql -X -q -v ON_ERROR_STOP=1 -tA -F $'\t' -v qv="$qv" -v q="$query" \
  -v scope="$scope" -v types="{$types_arr}" -v statuses="$statuses" -v k="$k" -v cols="$cols" \
  -f "$here/sql/evidence-search.sql"
