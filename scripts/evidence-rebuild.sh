#!/usr/bin/env bash
# evidence-rebuild.sh [--table NAME]
# Rebuild the career_docs pgvector table FROM its one source of truth,
# $JOBSEEK_DRIVE_DIR/evidence/career_docs.jsonl (one JSON object per line, no embedding).
# The table is a derived cache (docs/adr/0002-data-and-evidence.md): this script makes it a
# mirror of the file — new rows are inserted and embedded, changed rows re-embedded, rows
# whose content_hash left the file deleted. Safe to re-run; an unchanged file re-embeds nothing.
#
# Embeddings are computed IN-DB by embed(text) (nomic-embed-text via the local Ollama), over
# 'search_document: ' || content — the task prefix the model card says MUST be present
# (queries use 'search_query: ', see evidence.sh). Each row records that recipe in embed_spec;
# a row embedded under any other spec is re-embedded, so changing the recipe is one constant
# here plus one rebuild. embed() itself stays prefix-free: it is shared with other tables.
#
#   --table NAME   target table (default career_docs). Use a scratch name to rehearse:
#                  scripts/evidence-rebuild.sh --table career_docs_rehearsal
set -euo pipefail

table="career_docs"
while [ "$#" -gt 0 ]; do
  case "$1" in
  --table)
    table="${2:-}"
    shift 2
    ;;
  -h | --help)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "unknown arg: $1" >&2
    exit 2
    ;;
  esac
done
case "$table" in '' | *[!a-z0-9_]*)
  echo "error: --table must be [a-z0-9_]+" >&2
  exit 2
  ;;
esac

: "${RAGDB_URI:?set RAGDB_URI to the ragdb connection string (loopback pgvector store)}"
data="${JOBSEEK_DRIVE_DIR:?set JOBSEEK_DRIVE_DIR to the job-search folder in Drive}"
src="$data/evidence/career_docs.jsonl"
[ -f "$src" ] || {
  echo "error: no evidence/career_docs.jsonl under JOBSEEK_DRIVE_DIR" >&2
  exit 1
}
command -v psql >/dev/null 2>&1 || {
  echo "error: psql not found on PATH" >&2
  exit 1
}

# Validate every line parses and carries content + content_hash BEFORE touching the table,
# and that content_hash really is sha256(content) — a hand-edited line with a stale hash would
# otherwise skip its re-embed silently.
python3 -I - "$src" <<'PY'
import hashlib, json, sys
seen = set()
for n, line in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    if not line.strip():
        continue
    try:
        d = json.loads(line)
    except json.JSONDecodeError as e:
        sys.exit(f"line {n}: not JSON ({e})")
    c, h = d.get("content"), d.get("content_hash")
    if not c or not h:
        sys.exit(f"line {n}: missing content or content_hash")
    if hashlib.sha256(c.encode("utf-8")).hexdigest() != h:
        sys.exit(f"line {n}: content_hash is not sha256(content) — recompute it")
    if h in seen:
        sys.exit(f"line {n}: duplicate content_hash {h[:12]}")
    seen.add(h)
print(f"source OK: {len(seen)} rows", file=sys.stderr)
PY

# The SQL goes in a file so the JSONL can be psql's stdin (\copy ... FROM pstdin). It loads as
# a one-column CSV whose quote and delimiter bytes never occur in JSON text, so backslashes in
# the JSON survive (COPY's text format would un-escape them).
sql="$(mktemp)"
trap 'rm -f "$sql"' EXIT
cat >"$sql" <<'SQL'
CREATE TABLE IF NOT EXISTS :"tbl" (
  id                bigserial PRIMARY KEY,
  content           text NOT NULL,
  embedding         vector(768),
  company           text,
  repo              text,
  doc_type          text,
  title             text,
  skills            text[] DEFAULT '{}',
  technologies      text[] DEFAULT '{}',
  domains           text[] DEFAULT '{}',
  author_confidence text,
  period            text,
  content_hash      text UNIQUE NOT NULL,
  metadata          jsonb DEFAULT '{}'::jsonb,
  created_at        timestamptz DEFAULT now(),
  updated_at        timestamptz DEFAULT now(),
  source_class      text DEFAULT 'enterprise',
  embed_spec        text
);
ALTER TABLE :"tbl" ADD COLUMN IF NOT EXISTS embed_spec text;
-- The keyword half of the hybrid search in evidence.sh (docs/adr/0004).
ALTER TABLE :"tbl" ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(title, '') || ' ' || content)) STORED;
\set spec 'nomic-embed-text|search_document: '

CREATE TEMP TABLE stage (doc jsonb);
\copy stage (doc) FROM pstdin WITH (FORMAT csv, QUOTE E'\x01', DELIMITER E'\x02')

CREATE TEMP VIEW incoming AS
SELECT doc->>'content'           AS content,
       doc->>'content_hash'      AS content_hash,
       doc->>'company'           AS company,
       doc->>'repo'              AS repo,
       doc->>'doc_type'          AS doc_type,
       doc->>'title'             AS title,
       ARRAY(SELECT jsonb_array_elements_text(coalesce(doc->'skills', '[]')))       AS skills,
       ARRAY(SELECT jsonb_array_elements_text(coalesce(doc->'technologies', '[]'))) AS technologies,
       ARRAY(SELECT jsonb_array_elements_text(coalesce(doc->'domains', '[]')))      AS domains,
       doc->>'author_confidence' AS author_confidence,
       doc->>'period'            AS period,
       coalesce(doc->'metadata', '{}'::jsonb) AS metadata,
       coalesce(doc->>'source_class', 'enterprise') AS source_class,
       coalesce((doc->>'created_at')::timestamptz, now()) AS created_at
FROM stage;

BEGIN;
DELETE FROM :"tbl" t WHERE NOT EXISTS (SELECT 1 FROM incoming i WHERE i.content_hash = t.content_hash);

-- A row embedded under another recipe is re-embedded, whatever else changed.
UPDATE :"tbl" t SET embedding = embed('search_document: ' || t.content), embed_spec = :'spec'
WHERE t.embed_spec IS DISTINCT FROM :'spec';

-- Same hash = same content = same vector: refresh the metadata only, never re-embed.
UPDATE :"tbl" t SET company = i.company, repo = i.repo, doc_type = i.doc_type, title = i.title,
       skills = i.skills, technologies = i.technologies, domains = i.domains,
       author_confidence = i.author_confidence, period = i.period, metadata = i.metadata,
       source_class = i.source_class, updated_at = now()
FROM incoming i
WHERE i.content_hash = t.content_hash
  AND (t.company, t.repo, t.doc_type, t.title, t.skills, t.technologies, t.domains,
       t.author_confidence, t.period, t.metadata, t.source_class)
      IS DISTINCT FROM
      (i.company, i.repo, i.doc_type, i.title, i.skills, i.technologies, i.domains,
       i.author_confidence, i.period, i.metadata, i.source_class);

INSERT INTO :"tbl" (content, embedding, company, repo, doc_type, title, skills, technologies,
                    domains, author_confidence, period, content_hash, metadata, source_class,
                    created_at, embed_spec)
SELECT i.content, embed('search_document: ' || i.content), i.company, i.repo, i.doc_type, i.title, i.skills,
       i.technologies, i.domains, i.author_confidence, i.period, i.content_hash, i.metadata,
       i.source_class, i.created_at, :'spec'
FROM incoming i
WHERE NOT EXISTS (SELECT 1 FROM :"tbl" t WHERE t.content_hash = i.content_hash);
COMMIT;

\set idx :tbl '_embedding_hnsw'
CREATE INDEX IF NOT EXISTS :"idx" ON :"tbl" USING hnsw (embedding vector_cosine_ops);
\set tidx :tbl '_tsv_gin'
CREATE INDEX IF NOT EXISTS :"tidx" ON :"tbl" USING gin (tsv);
SELECT 'table ' || :'tbl' || ': ' || count(*) || ' rows, ' || count(embedding) || ' embedded' FROM :"tbl";
SQL
psql "$RAGDB_URI" -X -q -v ON_ERROR_STOP=1 -v tbl="$table" -f "$sql" <"$src"
