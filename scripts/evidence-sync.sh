#!/usr/bin/env bash
# evidence-sync.sh [--table NAME] [--dry-run] [--seed-from TABLE]
# The ONE evidence job (docs/adr/0007 § 2.3). Idempotent and level-triggered — run it any time:
#   1. intake    proposal files waiting in $JOBSEEK_DRIVE_DIR/evidence/inbox become pending
#                documents (scripts/evidence-store.py intake; fingerprint-checked, ADR § 3);
#   2. invariant each content_hash in exactly one state file, each doc_id once per file in approved and pending; repairs
#                what a crash left behind (ADR § 4);
#   3. mirror    approved.jsonl + pending.jsonl + rejected.jsonl -> the Neon table `evidence`
#                (PK content_hash; `status` column; doc_id indexed, not unique): new versions
#                inserted, changed rows updated, versions that left every file deleted;
#   4. embed     only versions not embedded under the CURRENT recipe (embed.py's SPEC), via
#                scripts/embed.py fill (nomic-embed-text on this Mac's Ollama);
#   5. record    each file's sha256 in `evidence_sync`, so a reader can detect drift;
#   6. gauges    best-effort: scripts/evidence-metrics.py pushes the store gauges to Grafana Cloud
#                (skipped silently when unconfigured; a failure only warns).
# Prints one counts line per run: "deleted X, updated Y, inserted Z, re-embed R" then "embedded E".
# A second run on unchanged files prints all zeros and embeds nothing.
#
# The table is a derived cache (docs/adr/0002): lose it and one sync rebuilds it.
#
#   --table NAME      target table (default evidence); its sync table is NAME_sync. Use a scratch
#                     name to rehearse: scripts/evidence-sync.sh --table evidence_rehearsal
#   --dry-run         steps 1-2 as a plan only: writes nothing, never touches Neon
#   --seed-from TBL   a version NEW to the table takes its vector and embed_spec from TBL
#                     (a table with content_hash, embedding, embed_spec; same content_hash)
#                     instead of being re-embedded.
#                     ADR-0007 § 9 used it once for the migration.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

dry=0
for arg in "$@"; do [ "$arg" = "--dry-run" ] && dry=1; done
if [ "$dry" = 0 ]; then
  # shellcheck source=db-env.sh
  . "$here/db-env.sh" "$@"
fi

table="evidence"
seed=""
while [ "$#" -gt 0 ]; do
  case "$1" in
  --table)
    table="${2:-}"
    shift 2
    ;;
  --seed-from)
    seed="${2:-}"
    shift 2
    ;;
  --dry-run) shift ;;
  -h | --help)
    sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "unknown arg: $1" >&2
    exit 2
    ;;
  esac
done
for name in "$table" "$seed"; do
  case "$name" in *[!a-z0-9_]*)
    echo "error: table names must be [a-z0-9_]+" >&2
    exit 2
    ;;
  esac
done
[ -n "$table" ] || {
  echo "error: --table needs a name" >&2
  exit 2
}
: "${JOBSEEK_DRIVE_DIR:?set JOBSEEK_DRIVE_DIR to the job-search folder in Drive}"
[ -f "$JOBSEEK_DRIVE_DIR/evidence/approved.jsonl" ] || {
  echo "error: no evidence/approved.jsonl under JOBSEEK_DRIVE_DIR (ADR-0007 migration not run?)" >&2
  exit 1
}
store=(python3 -I "$here/evidence-store.py")

if [ "$dry" = 1 ]; then
  "${store[@]}" sync-prep --dry-run --stage /dev/null --shas /dev/null
  exit
fi
command -v psql >/dev/null 2>&1 || {
  echo "error: psql not found on PATH" >&2
  exit 1
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# Steps 1-2, and the one consistent read of the three files that steps 3-5 mirror.
"${store[@]}" sync-prep --stage "$work/stage.jsonl" --shas "$work/shas.tsv"

# The embedding recipe has ONE definition, embed.py's SPEC — read it, never retype it.
spec="$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import embed; print(embed.SPEC)' "$here")"
seedsrc='(SELECT NULL::text AS content_hash, NULL::vector(768) AS embedding, NULL::text AS embed_spec WHERE false)'
[ -z "$seed" ] || seedsrc="public.$seed"
sha_of() { awk -F'\t' -v s="$1" '$1 == s { print $2 }' "$work/shas.tsv"; }
rows_of() { awk -F'\t' -v s="$1" '$1 == s { print $3 }' "$work/shas.tsv"; }

sql="$work/mirror.sql"
cat >"$sql" <<'SQL'
SET client_min_messages = warning;
CREATE TABLE IF NOT EXISTS :"tbl" (
  content_hash      text PRIMARY KEY,
  doc_id            uuid NOT NULL,
  status            text NOT NULL CHECK (status IN ('approved', 'pending', 'rejected')),
  content           text NOT NULL,
  embedding         vector(768),
  company           text,
  repo              text,
  doc_type          text,
  title             text,
  skills            text[] NOT NULL DEFAULT '{}',
  technologies      text[] NOT NULL DEFAULT '{}',
  domains           text[] NOT NULL DEFAULT '{}',
  author_confidence text,
  period            text,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz,
  updated_at        timestamptz,
  source_class      text DEFAULT 'enterprise',
  embed_spec        text,
  -- The keyword half of the hybrid search in evidence.sh (docs/adr/0004).
  tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title, '') || ' ' || content)) STORED
);
CREATE TABLE IF NOT EXISTS :"syn" (
  file      text PRIMARY KEY,
  sha256    text NOT NULL,
  row_count integer NOT NULL,
  synced_at timestamptz NOT NULL DEFAULT now()
);
-- The cloud routine reads through the read-only evidence_reader role (match.py). A table rebuilt
-- by DROP + CREATE loses its grants, so re-grant on every run (idempotent). Skipped when the role
-- does not exist (a scratch database).
SELECT format('GRANT SELECT ON %I, %I TO evidence_reader', :'tbl', :'syn')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evidence_reader') \gexec
\set i_hnsw :tbl '_embedding_hnsw'
\set i_tsv :tbl '_tsv_gin'
\set i_doc :tbl '_doc_id_idx'
\set i_status :tbl '_status_idx'
\set i_company :tbl '_company_idx'
\set i_doctype :tbl '_doctype_idx'
\set i_repo :tbl '_repo_idx'
\set i_src :tbl '_srcclass_idx'
\set i_skills :tbl '_skills_gin'
\set i_tech :tbl '_tech_gin'
\set i_meta :tbl '_meta_gin'
CREATE INDEX IF NOT EXISTS :"i_hnsw" ON :"tbl" USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS :"i_tsv" ON :"tbl" USING gin (tsv);
CREATE INDEX IF NOT EXISTS :"i_doc" ON :"tbl" (doc_id);
CREATE INDEX IF NOT EXISTS :"i_status" ON :"tbl" (status);
CREATE INDEX IF NOT EXISTS :"i_company" ON :"tbl" (company);
CREATE INDEX IF NOT EXISTS :"i_doctype" ON :"tbl" (doc_type);
CREATE INDEX IF NOT EXISTS :"i_repo" ON :"tbl" (repo);
CREATE INDEX IF NOT EXISTS :"i_src" ON :"tbl" (source_class);
CREATE INDEX IF NOT EXISTS :"i_skills" ON :"tbl" USING gin (skills);
CREATE INDEX IF NOT EXISTS :"i_tech" ON :"tbl" USING gin (technologies);
CREATE INDEX IF NOT EXISTS :"i_meta" ON :"tbl" USING gin (metadata);

-- One merged stream from evidence-store.py: every document of the three files, with its status.
-- Loaded as a one-column CSV whose quote and delimiter bytes never occur in JSON text, so
-- backslashes in the JSON survive (COPY's text format would un-escape them).
CREATE TEMP TABLE stage (doc jsonb);
\copy stage (doc) FROM pstdin WITH (FORMAT csv, QUOTE E'\x01', DELIMITER E'\x02')

CREATE TEMP VIEW incoming AS
SELECT doc->>'content_hash'      AS content_hash,
       (doc->>'doc_id')::uuid    AS doc_id,
       doc->>'status'            AS status,
       doc->>'content'           AS content,
       doc->>'company'           AS company,
       doc->>'repo'              AS repo,
       doc->>'doc_type'          AS doc_type,
       doc->>'title'             AS title,
       ARRAY(SELECT jsonb_array_elements_text(coalesce(doc->'skills', '[]')))       AS skills,
       ARRAY(SELECT jsonb_array_elements_text(coalesce(doc->'technologies', '[]'))) AS technologies,
       ARRAY(SELECT jsonb_array_elements_text(coalesce(doc->'domains', '[]')))      AS domains,
       doc->>'author_confidence' AS author_confidence,
       doc->>'period'            AS period,
       coalesce(doc->'metadata', '{}'::jsonb)      AS metadata,
       doc->>'source_class'      AS source_class,
       (doc->>'created_at')::timestamptz AS created_at,
       (doc->>'updated_at')::timestamptz AS updated_at
FROM stage;

BEGIN;
-- Versions that left every file.
WITH x AS (DELETE FROM :"tbl" t WHERE NOT EXISTS (SELECT 1 FROM incoming i WHERE i.content_hash = t.content_hash) RETURNING 1)
SELECT count(*) AS n_del FROM x \gset

-- A row embedded under another recipe is re-embedded, whatever else changed.
WITH x AS (UPDATE :"tbl" SET embedding = NULL, embed_spec = NULL
           WHERE embed_spec IS DISTINCT FROM :'spec' AND (embedding IS NOT NULL OR embed_spec IS NOT NULL) RETURNING 1)
SELECT count(*) AS n_reset FROM x \gset

-- Same hash = same content = same vector: refresh the metadata only, never re-embed.
WITH x AS (UPDATE :"tbl" t SET doc_id = i.doc_id, status = i.status, company = i.company, repo = i.repo,
           doc_type = i.doc_type, title = i.title, skills = i.skills, technologies = i.technologies,
           domains = i.domains, author_confidence = i.author_confidence, period = i.period,
           metadata = i.metadata, source_class = i.source_class, created_at = i.created_at,
           updated_at = i.updated_at
           FROM incoming i
           WHERE i.content_hash = t.content_hash
             AND (t.doc_id, t.status, t.company, t.repo, t.doc_type, t.title, t.skills, t.technologies,
                  t.domains, t.author_confidence, t.period, t.metadata, t.source_class, t.created_at,
                  t.updated_at)
                 IS DISTINCT FROM
                 (i.doc_id, i.status, i.company, i.repo, i.doc_type, i.title, i.skills, i.technologies,
                  i.domains, i.author_confidence, i.period, i.metadata, i.source_class, i.created_at,
                  i.updated_at)
           RETURNING 1)
SELECT count(*) AS n_upd FROM x \gset

-- New versions: no vector yet (embed.py fill below) unless --seed-from supplies one.
WITH x AS (INSERT INTO :"tbl" (content_hash, doc_id, status, content, embedding, company, repo, doc_type,
                               title, skills, technologies, domains, author_confidence, period, metadata,
                               source_class, created_at, updated_at, embed_spec)
           SELECT i.content_hash, i.doc_id, i.status, i.content, s.embedding, i.company, i.repo, i.doc_type,
                  i.title, i.skills, i.technologies, i.domains, i.author_confidence, i.period, i.metadata,
                  i.source_class, i.created_at, i.updated_at, s.embed_spec
           FROM incoming i LEFT JOIN :seedsrc s ON s.content_hash = i.content_hash
           WHERE NOT EXISTS (SELECT 1 FROM :"tbl" t WHERE t.content_hash = i.content_hash)
           RETURNING 1)
SELECT count(*) AS n_ins FROM x \gset

-- The mirror must hold exactly the staged versions; a mismatch divides by zero and rolls back.
SELECT (SELECT count(*) FROM :"tbl") <> (SELECT count(*) FROM stage) AS mismatch \gset
SELECT 1 / CASE WHEN :'mismatch'::boolean THEN 0 ELSE 1 END AS mirror_row_count_matches_the_files \g /dev/null
COMMIT;

SELECT format('deleted %s, updated %s, inserted %s, re-embed %s', :n_del, :n_upd, :n_ins, :n_reset);
SQL
psql -X -q -tA -v ON_ERROR_STOP=1 -v tbl="$table" -v syn="${table}_sync" \
  -v spec="$spec" -v seedsrc="$seedsrc" -f "$sql" <"$work/stage.jsonl"

# $table is validated [a-z0-9_]+ above, so it is safe to interpolate (psql -c does not substitute -v variables).
todo() { psql -X -tA -c "SELECT count(*) FROM $table WHERE embedding IS NULL"; }
before="$(todo)"
if [ "$before" -gt 0 ]; then
  python3 "$here/embed.py" fill "$table" content
fi
left="$(todo)"
echo "embedded $((before - left))"
[ "$left" = 0 ] || {
  echo "error: $left row(s) still have no vector" >&2
  exit 1
}

# Record what was mirrored — only now, so a sync that died before embedding reads as drift.
psql -X -q -v ON_ERROR_STOP=1 -v syn="${table}_sync" \
  -v a="$(sha_of approved)" -v an="$(rows_of approved)" \
  -v p="$(sha_of pending)" -v pn="$(rows_of pending)" \
  -v r="$(sha_of rejected)" -v rn="$(rows_of rejected)" <<'SQL'
INSERT INTO :"syn" AS e (file, sha256, row_count)
VALUES ('approved.jsonl', :'a', :an), ('pending.jsonl', :'p', :pn), ('rejected.jsonl', :'r', :rn)
ON CONFLICT (file) DO UPDATE SET sha256 = excluded.sha256, row_count = excluded.row_count, synced_at = now()
WHERE (e.sha256, e.row_count) IS DISTINCT FROM (excluded.sha256, excluded.row_count);
SQL
psql -X -tA -v tbl="$table" <<'SQL'
SELECT 'table ' || :'tbl' || ': ' || count(*) || ' rows (approved ' || count(*) FILTER (WHERE status = 'approved')
       || ', pending ' || count(*) FILTER (WHERE status = 'pending')
       || ', rejected ' || count(*) FILTER (WHERE status = 'rejected') || '), ' || count(embedding) || ' embedded'
FROM :"tbl";
SQL

# Best-effort gauges for Grafana (docs/adr/0007 § 5): a push problem never fails the sync. Exit 3 means
# push is not configured here (no endpoint, or the Keychain item is missing), which is silent on purpose.
python3 -I "$here/evidence-metrics.py" || {
  rc=$?
  [ "$rc" = 3 ] || echo "warning: evidence-metrics push failed (exit $rc); the sync itself succeeded" >&2
}
