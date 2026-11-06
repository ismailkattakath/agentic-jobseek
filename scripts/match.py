#!/usr/bin/env python3
"""match.py [--k N] < groups.tsv — nearest evidence documents for each group, as JSON lines.

The evidence-discover routine's Match step (routines/, docs/adr/0006, docs/adr/0007 § 2.6). Input:
one `key<TAB>sentence` per line. Every sentence is embedded in one batch with the cloud
environment's cached fastembed nomic-embed-text-v1.5 ('search_query: ' prefix — the same vectors
as the local Ollama), ranked by the shared hybrid search (scripts/sql/evidence-search.sql), and
printed as {"key", "sentence", "neighbours": [{"doc_id", "content_hash", "title", "cosine",
"status", "repo", "period"}]}. The search covers all three states (approved, pending, rejected), so
new work on a pending item becomes an enrichment of it and rejected work is not proposed again;
neighbours are VERSIONS, so a pending enrichment and its approved base can share a doc_id.

Vectors never pass through the model: the query goes straight to Neon's HTTPS SQL endpoint
(https://$JOBSEEK_NEON_SQL_HOST/sql) as the read-only `evidence_reader` role. In the cloud the
environment's network secret adds that role's `Neon-Connection-String` header; locally, set
NEON_CONNECTION_STRING (e.g. `secret exec NEON_CONNECTION_STRING=<item> -- …`).
"""

import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "sql_render", os.path.join(HERE, "sql-render.py")
)
sql_render = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sql_render)

TYPES = "{contribution,hard_problem,skill_evidence,architecture_decision,unique_strength,subsystem,cross_repo_theme}"
STATUSES = "{approved,pending,rejected}"
COLS = (
    "d.doc_id, d.content_hash, d.title, "
    "(SELECT round((1 - min(c.embedding <=> q.v))::numeric, 4) FROM public.evidence_chunk c WHERE c.content_hash = d.content_hash) AS cosine, d.status, d.repo, d.period"
)


def query(host, sql):
    headers = {"Content-Type": "application/json", "Neon-Raw-Text-Output": "true"}
    if os.environ.get("NEON_CONNECTION_STRING"):
        headers["Neon-Connection-String"] = os.environ["NEON_CONNECTION_STRING"]
    req = urllib.request.Request(
        f"https://{host}/sql",
        headers=headers,
        data=json.dumps({"query": sql, "params": []}).encode(),
    )
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — the Neon endpoint
        return json.load(r)["rows"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--k", type=int, default=3)
    a = ap.parse_args()
    host = os.environ.get("JOBSEEK_NEON_SQL_HOST") or sys.exit(
        "error: set JOBSEEK_NEON_SQL_HOST"
    )
    groups = [line.rstrip("\n").split("\t", 1) for line in sys.stdin if line.strip()]
    if any(len(g) != 2 for g in groups):
        sys.exit("error: each input line must be key<TAB>sentence")
    from fastembed import TextEmbedding

    model = TextEmbedding("nomic-ai/nomic-embed-text-v1.5", max_length=2048)
    vectors = model.embed(["search_query: " + s for _, s in groups])
    sql_file = open(
        os.path.join(HERE, "sql", "evidence-search.sql"), encoding="utf-8"
    ).read()
    for (key, sentence), v in zip(groups, vectors):
        sql = sql_render.render(
            sql_file,
            {
                "qv": "[" + ",".join(repr(float(x)) for x in v) + "]",
                "q": sentence,
                "scope": "",
                "types": TYPES,
                "statuses": STATUSES,
                "k": str(a.k),
                "cols": COLS,
            },
        )
        rows = query(host, sql)
        for r in rows:
            r["cosine"] = float(r["cosine"])
        print(
            json.dumps(
                {"key": key, "sentence": sentence, "neighbours": rows},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
