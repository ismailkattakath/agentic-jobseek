#!/usr/bin/env bash
# match-sql.sh "<sentence>" [k] — print the statement the evidence-refresh routine sends to Neon's
# run_sql: the shared hybrid search (scripts/sql/evidence-search.sql) for one sentence, k = 3,
# returning content_hash, title, repo, period and cosine. The sentence is embedded with the cloud
# environment's cached fastembed nomic-embed-text-v1.5 (same vectors as the local Ollama; ADR-0006),
# rounded to four decimals so the statement stays ~6 KB.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
sentence="${1:?usage: scripts/match-sql.sh \"<sentence>\" [k]}"
k="${2:-3}"
py="${FASTEMBED_PYTHON:-/opt/fe/bin/python}"
qv=$("$py" -c 'import sys
from fastembed import TextEmbedding
v = list(TextEmbedding("nomic-ai/nomic-embed-text-v1.5", max_length=2048).embed(["search_query: " + sys.argv[1]]))[0]
print("[" + ",".join("%.4f" % x for x in v) + "]")' "$sentence")
python3 "$here/sql-render.py" "$here/sql/evidence-search.sql" "qv=$qv" "q=$sentence" scope= \
  "types={contribution,hard_problem,skill_evidence,architecture_decision,unique_strength,subsystem,cross_repo_theme}" \
  "k=$k" "cols=d.content_hash, d.title, d.repo, d.period, round((1 - (d.embedding <=> q.v))::numeric, 4)"
