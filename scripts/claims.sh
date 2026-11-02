#!/usr/bin/env bash
# claims.sh rebuild | search "<draft bullet or requirement>" [--k N]
# The claims ledger: every résumé claim a fact-check has ruled on, with its verdict, the wording
# to use, and the evidence titles behind it. Source of truth: $JOBSEEK_DRIVE_DIR/evidence/claims.jsonl
# (one JSON object per line, written by the tailor's final pass — see the tailor-application skill).
# The claims pgvector table is a CACHE of it, like evidence (docs/adr/0002):
#   rebuild  mirror the table from the JSONL (embeds only new or changed claims)
#   search   the nearest past rulings for a draft bullet — reuse a vetted wording, or learn it was
#            already rejected, before the fact-checker has to find it again
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=db-env.sh
. "$here/db-env.sh" "$@"
src="${JOBSEEK_DRIVE_DIR:?set JOBSEEK_DRIVE_DIR to the job-search folder in Drive}/evidence/claims.jsonl"
cmd="${1:-}"
shift || true

case "$cmd" in
rebuild)
  [ -f "$src" ] || {
    echo "error: no evidence/claims.jsonl under JOBSEEK_DRIVE_DIR" >&2
    exit 1
  }
  python3 -I - "$src" <<'PY'
import hashlib, json, sys
need = {"claim", "verdict", "content_hash"}
verdicts = {"backed", "narrowed", "teammate", "gap", "stale", "unverifiable"}
seen = set()
for n, line in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    if not line.strip():
        continue
    d = json.loads(line)
    if not need <= d.keys():
        sys.exit(f"line {n}: needs {sorted(need)}")
    if d["verdict"] not in verdicts:
        sys.exit(f"line {n}: verdict must be one of {sorted(verdicts)}")
    if hashlib.sha256(d["claim"].encode("utf-8")).hexdigest() != d["content_hash"]:
        sys.exit(f"line {n}: content_hash is not sha256(claim)")
    if d["content_hash"] in seen:
        sys.exit(f"line {n}: duplicate claim")
    seen.add(d["content_hash"])
print(f"source OK: {len(seen)} claims", file=sys.stderr)
PY
  sql="$(mktemp)"
  trap 'rm -f "$sql"' EXIT
  cat >"$sql" <<'SQL'
CREATE TABLE IF NOT EXISTS claims (
  content_hash text PRIMARY KEY,
  claim        text NOT NULL,
  doc          jsonb NOT NULL,
  embedding    vector(768),
  embed_spec   text
);
ALTER TABLE claims ADD COLUMN IF NOT EXISTS embed_spec text;
CREATE TEMP TABLE stage (doc jsonb);
\copy stage (doc) FROM pstdin WITH (FORMAT csv, QUOTE E'\x01', DELIMITER E'\x02')
BEGIN;
-- Rows made under another recipe are re-embedded by scripts/embed.py fill (see evidence-sync.sh).
UPDATE claims SET embedding = NULL, embed_spec = NULL
 WHERE embed_spec IS DISTINCT FROM 'nomic-embed-text|search_document: ';
DELETE FROM claims c WHERE NOT EXISTS (SELECT 1 FROM stage s WHERE s.doc->>'content_hash' = c.content_hash);
UPDATE claims c SET doc = s.doc FROM stage s
 WHERE s.doc->>'content_hash' = c.content_hash AND c.doc IS DISTINCT FROM s.doc;
INSERT INTO claims (content_hash, claim, doc, embedding, embed_spec)
SELECT s.doc->>'content_hash', s.doc->>'claim', s.doc, NULL, NULL FROM stage s
 WHERE NOT EXISTS (SELECT 1 FROM claims c WHERE c.content_hash = s.doc->>'content_hash');
COMMIT;
SQL
  psql -X -q -v ON_ERROR_STOP=1 -tA -f "$sql" <"$src"
  python3 "$here/embed.py" fill claims claim
  psql -X -tA -c "SELECT 'claims: ' || count(*) || ' claims, ' || count(embedding) || ' embedded' FROM claims"
  ;;
search)
  query="${1:?usage: scripts/claims.sh search \"<text>\" [--k N]}"
  k=5
  [ "${2:-}" = "--k" ] && k="${3:-5}"
  case "$k" in '' | *[!0-9]*)
    echo "error: --k must be an integer" >&2
    exit 2
    ;;
  esac
  want=$(grep -c . "$src" 2>/dev/null || echo 0)
  have=$(psql -X -tA -c "SELECT count(*) FROM claims" 2>/dev/null || echo missing)
  [ "$have" = "$want" ] || {
    echo "error: claims has $have rows but the source has $want — run scripts/claims.sh rebuild" >&2
    exit 1
  }
  # The query is embedded on this Mac (scripts/embed.py); the cache in Neon cannot reach Ollama.
  qv=$(python3 "$here/embed.py" query "$query")
  printf '%s\n' "WITH q AS MATERIALIZED (SELECT :'qv'::vector AS v)
  SELECT doc->>'verdict', round((1 - (embedding <=> q.v))::numeric, 2),
       doc->>'claim', coalesce(doc->>'use', ''), coalesce(doc->>'note', '')
  FROM claims, q ORDER BY embedding <=> q.v LIMIT :k;" |
    psql -X -q -v ON_ERROR_STOP=1 -tA -F $'\t' -v qv="$qv" -v k="$k"
  ;;
*)
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
  ;;
esac
