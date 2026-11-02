#!/usr/bin/env bash
# evidence-dups.sh [--threshold 0.94]
# Near-duplicate report for the evidence store: every pair of career_docs whose embeddings are
# at least <threshold> cosine-similar, most similar first. Exact duplicates are already
# impossible (content_hash is unique); a near-duplicate is the same work written twice, which
# double-counts in a top-k. The report decides nothing: merge a real duplicate in
# $JOBSEEK_DRIVE_DIR/evidence/career_docs.jsonl (keep the more precise doc, fold in the other's
# unique facts, delete the other line), then scripts/evidence-rebuild.sh. An overview doc and
# a detail doc about the same system are NOT duplicates — keep both.
# Threshold 0.94, measured 2026-11-01 under the prefixed embeddings (search_document:), which raise
# every score (raw embeddings: 5 pairs >= 0.92). The two genuine duplicates found that day scored
# 0.932 raw and 0.947 prefixed; above 0.95 sat only overview-vs-overview pairs and one pattern
# reused in two repos. Expect to read a dozen pairs and merge few.
set -euo pipefail

threshold=0.94
[ "${1:-}" = "--threshold" ] && threshold="${2:?}"
case "$threshold" in 0.[0-9]* | 1) ;; *)
  echo "error: --threshold must be between 0 and 1" >&2
  exit 2
  ;;
esac
: "${RAGDB_URI:?set RAGDB_URI to the ragdb connection string}"

printf '%s\n' "SELECT round((1 - (a.embedding <=> b.embedding))::numeric, 3), a.id, b.id, a.title, b.title
  FROM career_docs a JOIN career_docs b ON a.id < b.id
  WHERE 1 - (a.embedding <=> b.embedding) >= :thr
  ORDER BY a.embedding <=> b.embedding;" |
  psql "$RAGDB_URI" -X -q -v ON_ERROR_STOP=1 -tA -F $'\t' -v thr="$threshold" |
  awk -F'\t' 'BEGIN { n = 0 } { n++; printf "%s  #%s %s\n       #%s %s\n", $1, $2, $4, $3, $5 } END { printf "%d pair(s) at >= '"$threshold"'\n", n > "/dev/stderr" }'
