#!/usr/bin/env python3
"""embed.py query "<text>" | fill <table> <text-column> [--vec COL] [--prefix P] [--no-spec]

nomic-embed-text vectors, computed on the client (docs/adr/0006). The cache moved to Neon, which
cannot reach the local Ollama, so no script embeds in SQL any more.

  query   print the 'search_query: ' vector of <text> as a pgvector literal
  fill    embed every row of <table> whose <vec> column is NULL (default: embedding, prefix
          'search_document: ', recording the recipe in embed_spec) and write the vectors back.
          --no-spec skips embed_spec for tables without one (eval_docs).

Calls Ollama exactly as the retired in-database embed() did — POST /api/embeddings
{"model": "nomic-embed-text", "prompt": ...} — so new vectors match the stored ones. Parity with
the cloud run's fastembed nomic-embed-text-v1.5 was measured 2026-11-02 at cosine
0.999999–1.000001 against these vectors. The connection string comes from JOBSEEK_DB_URI.
"""

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

MODEL = "nomic-embed-text"
SPEC = f"{MODEL}|search_document: "
OLLAMA = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").rstrip("/")
if "://" not in OLLAMA:  # Ollama's own convention is a bare host:port
    OLLAMA = "http://" + OLLAMA


def db_uri():
    """JOBSEEK_DB_URI, or re-run this script under `secret exec` so the password stays out of the shell."""
    uri = os.environ.get("JOBSEEK_DB_URI")
    if uri:  # direct, not through Neon's pooler — see scripts/db-env.sh
        return uri.replace("-pooler.", ".", 1)
    item = os.environ.get("JOBSEEK_DB_SECRET") or sys.exit(
        "error: set JOBSEEK_DB_SECRET (Keychain item) or JOBSEEK_DB_URI"
    )
    os.execvp(
        "secret",
        ["secret", "exec", f"JOBSEEK_DB_URI={item}", "--", sys.executable, *sys.argv],
    )


def embed(text):
    req = urllib.request.Request(
        f"{OLLAMA}/api/embeddings",
        data=json.dumps({"model": MODEL, "prompt": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 — loopback Ollama
        vec = json.load(r)["embedding"]
    if len(vec) != 768:
        sys.exit(f"error: {MODEL} returned {len(vec)} dimensions, expected 768")
    return vec


def literal(vec):
    return "[" + ",".join(repr(x) for x in vec) + "]"


def psql(uri, script, stdin=""):
    """Run a psql script from a file, so `\\copy ... pstdin` can read `stdin`."""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8") as f:
        f.write(script)
        f.flush()
        out = subprocess.run(
            ["psql", uri, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-1", "-f", f.name],
            input=stdin,
            capture_output=True,
            text=True,
        )
    if out.returncode:
        sys.exit(out.stderr)
    return out.stdout


def fill(table, col, vec, prefix, spec):
    uri = db_uri()
    for name in (table, col, vec):
        if not name.replace("_", "").isalnum():
            sys.exit(f"error: bad identifier {name!r}")
    rows = list(
        csv.reader(
            io.StringIO(
                psql(
                    uri,
                    f"\\copy (SELECT content_hash, {col} FROM {table} WHERE {vec} IS NULL) TO STDOUT WITH (FORMAT csv)\n",
                )
            )
        )
    )
    if not rows:
        print(f"{table}: nothing to embed", file=sys.stderr)
        return
    buf = io.StringIO()
    w = csv.writer(buf)
    for i, (h, text) in enumerate(rows, 1):
        w.writerow([h, literal(embed(prefix + text))])
        if i % 50 == 0:
            print(f"  embedded {i}/{len(rows)}", file=sys.stderr)
    set_spec = f", embed_spec = '{SPEC}'" if spec else ""
    psql(
        uri,
        "CREATE TEMP TABLE v (h text, e text);\n"
        "\\copy v FROM pstdin WITH (FORMAT csv)\n"
        f"UPDATE {table} t SET {vec} = v.e::vector{set_spec} FROM v WHERE t.content_hash = v.h;\n",
        buf.getvalue(),
    )
    print(f"{table}: embedded {len(rows)} rows", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query")
    q.add_argument("text")
    f = sub.add_parser("fill")
    f.add_argument("table")
    f.add_argument("column")
    f.add_argument("--vec", default="embedding")
    f.add_argument("--prefix", default="search_document: ")
    f.add_argument("--no-spec", action="store_true")
    a = ap.parse_args()
    if a.cmd == "query":
        print(literal(embed("search_query: " + a.text)))
    else:
        fill(a.table, a.column, a.vec, a.prefix, not a.no_spec)


if __name__ == "__main__":
    main()
