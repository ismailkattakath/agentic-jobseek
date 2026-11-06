#!/usr/bin/env python3
"""embed.py query "<text>" | fill <table> <text-column> [--vec COL] [--prefix P] [--no-spec] | pgenv

nomic-embed-text vectors, computed on the client (docs/adr/0006). The cache moved to Neon, which
cannot reach the local Ollama, so no script embeds in SQL any more.

  query   print the 'search_query: ' vector of <text> as a pgvector literal
  fill    embed every row of <table> whose <vec> column is NULL (default: embedding, prefix
          'search_document: ', recording the recipe in embed_spec) and write the vectors back.
          --no-spec skips embed_spec for tables without one (eval_docs).
  pgenv   print the connection as `export PG*=…` lines for `eval` (scripts/db-env.sh does this),
          so no psql call ever carries the connection string — or its password — in argv.

Calls Ollama's POST /api/embed {"model": "nomic-embed-text", "input": ..., "truncate": true}.
It replaced the retired in-database embed()'s /api/embeddings on 2026-11-06, when merged pending
documents outgrew the 2,048-token window; the vectors point the same way (cosine 1.000000 on
stored rows), only unit-length, and every query ranks by cosine. Parity with
the cloud run's fastembed nomic-embed-text-v1.5 was measured 2026-11-02 at cosine
0.999999–1.000001 against these vectors. The connection string comes from JOBSEEK_DB_URI and
reaches psql only as PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE/… in its environment: a process
argument is world-readable through `ps` (measured 2026-11-02), a process environment is not.
"""

import argparse
import csv
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import parse_qsl, unquote, urlsplit

MODEL = "nomic-embed-text"
SPEC = f"{MODEL}|search_document: "
OLLAMA = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434").rstrip("/")
if "://" not in OLLAMA:  # Ollama's own convention is a bare host:port
    OLLAMA = "http://" + OLLAMA


# URI query parameter -> libpq environment variable (libpq docs, § Environment Variables).
PG_PARAMS = {
    "sslmode": "PGSSLMODE",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "options": "PGOPTIONS",
}


def pg_env(uri):
    """A postgres:// URI as the libpq environment variables that carry the same connection.

    Direct, not through Neon's pooler (the stored URI's host ends in "-pooler"): see db-env.sh.
    A query parameter this table does not know is an error naming the parameter, never its value.
    """
    u = urlsplit(uri.replace("-pooler.", ".", 1))
    if u.scheme not in ("postgres", "postgresql") or not u.hostname:
        sys.exit("error: the connection string is not a postgres:// URI")
    env = {
        "PGHOST": u.hostname,
        "PGPORT": str(u.port or 5432),
        "PGDATABASE": unquote(u.path.lstrip("/")) or "postgres",
        "PGSSLMODE": "require",
    }
    if u.username:
        env["PGUSER"] = unquote(u.username)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    for k, v in parse_qsl(u.query):
        if k not in PG_PARAMS:
            sys.exit(
                f"error: connection string parameter {k!r} has no libpq environment variable"
            )
        env[PG_PARAMS[k]] = v
    return env


def db_env():
    """os.environ plus the PG* connection variables, for a psql subprocess.

    Uses JOBSEEK_DB_URI, or re-runs this script under `secret exec` so the password stays out of the shell.
    """
    uri = os.environ.get("JOBSEEK_DB_URI")
    if uri:
        return {**os.environ, **pg_env(uri)}
    item = os.environ.get("JOBSEEK_DB_SECRET") or sys.exit(
        "error: set JOBSEEK_DB_SECRET (Keychain item) or JOBSEEK_DB_URI"
    )
    os.execvp(
        "secret",
        ["secret", "exec", f"JOBSEEK_DB_URI={item}", "--", sys.executable, *sys.argv],
    )


def embed(text):
    # /api/embed with truncate, not the legacy /api/embeddings: a document longer than the
    # model's 2,048-token window is cut to it, as the cloud's fastembed (max_length=2048) does,
    # instead of failing with HTTP 500. Vectors come back unit-length; every query ranks by
    # cosine (<=>), so the 2026-11-06 check showed cosine 1.000000 against the old vectors.
    req = urllib.request.Request(
        f"{OLLAMA}/api/embed",
        data=json.dumps({"model": MODEL, "input": text, "truncate": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 — loopback Ollama
        vec = json.load(r)["embeddings"][0]
    if len(vec) != 768:
        sys.exit(f"error: {MODEL} returned {len(vec)} dimensions, expected 768")
    return vec


def literal(vec):
    return "[" + ",".join(repr(x) for x in vec) + "]"


def psql(env, script, stdin=""):
    """Run a psql script from a file, so `\\copy ... pstdin` can read `stdin`."""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8") as f:
        f.write(script)
        f.flush()
        out = subprocess.run(
            ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-1", "-f", f.name],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
        )
    if out.returncode:
        sys.exit(out.stderr)
    return out.stdout


def fill(table, col, vec, prefix, spec):
    env = db_env()
    for name in (table, col, vec):
        if not name.replace("_", "").isalnum():
            sys.exit(f"error: bad identifier {name!r}")
    rows = list(
        csv.reader(
            io.StringIO(
                psql(
                    env,
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
        env,
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
    sub.add_parser("pgenv")
    a = ap.parse_args()
    if a.cmd == "pgenv":
        uri = os.environ.get("JOBSEEK_DB_URI") or sys.exit(
            "error: JOBSEEK_DB_URI is not set"
        )
        for k, v in pg_env(uri).items():
            print(f"export {k}={shlex.quote(v)}")
    elif a.cmd == "query":
        print(literal(embed("search_query: " + a.text)))
    else:
        fill(a.table, a.column, a.vec, a.prefix, not a.no_spec)


if __name__ == "__main__":
    main()
