#!/usr/bin/env python3
"""retrieval-eval.py pool [--k N] | score

Measure evidence retrieval against a graded test set, so a retrieval change (prefixes, hybrid,
reranking) is decided by a number. Data lives in $JOBSEEK_DRIVE_DIR/evidence/eval/:
  queries.jsonl  {"qid", "query", "source"}             — what a tailor actually asks
  qrels.jsonl    {"qid", "content_hash", "grade": 0|1|2} — judged relevance (2 = proves it,
                                                          1 = related, 0 = not relevant)
Method: TREC-style pooling — each system's top-k is unioned per query and only the pool is
judged; an unjudged document counts as grade 0.

Systems run against career_docs itself. "hybrid" is scripts/sql/evidence-search.sql with
evidence.sh's defaults — the very query tailoring uses, not a re-implementation (until
2026-11-02 this script fused its own copy, which scored 0.703 while production scored 0.727;
docs/adr/0006).

  pool    print the union of every system's top-k per query, as JSONL to judge.
  score   nDCG@10, Recall@10 (grade >= 1), Success@3 (a grade-2 doc in the top 3) and MRR
          per system.
"""

import argparse
import json
import math
import os
import subprocess
import sys

from embed import db_uri, embed, literal

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(
    os.environ.get("JOBSEEK_DRIVE_DIR") or sys.exit("error: set JOBSEEK_DRIVE_DIR"),
    "evidence",
    "eval",
)
TYPES = "{contribution,hard_problem,skill_evidence,architecture_decision,unique_strength,subsystem,cross_repo_theme}"
TSQ = "to_tsquery('english', array_to_string(tsvector_to_array(to_tsvector('english', :'q')), ' | '))"
SYSTEMS = {
    "vector": "SELECT content_hash FROM public.career_docs ORDER BY embedding <=> :'qv'::vector LIMIT :k;",
    "bm25": f"SELECT content_hash FROM public.career_docs WHERE tsv @@ {TSQ} ORDER BY ts_rank_cd(tsv, {TSQ}) DESC LIMIT :k;",
    "hybrid": open(
        os.path.join(HERE, "sql", "evidence-search.sql"), encoding="utf-8"
    ).read(),
}


def sql(uri, text, **v):
    args = ["psql", uri, "-X", "-q", "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1"]
    for k, val in v.items():
        args += ["-v", f"{k}={val}"]
    out = subprocess.run(args, input=text, capture_output=True, text=True)
    if out.returncode:
        sys.exit(out.stderr)
    return [line.split("\t") for line in out.stdout.splitlines() if line]


def run(uri, system, query, qv, k):
    return [
        r[0]
        for r in sql(
            uri,
            SYSTEMS[system],
            q=query,
            qv=qv,
            k=k,
            scope="",
            types=TYPES,
            cols="d.content_hash",
        )
    ]


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["pool", "score"])
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    uri = db_uri()
    systems = list(SYSTEMS)
    vectors = {}

    def ranked(s, query, k):
        if query not in vectors:  # one embedding per query, shared by every system
            vectors[query] = literal(embed("search_query: " + query))
        return run(uri, s, query, vectors[query], k)

    if args.cmd == "pool":
        src = os.path.join(os.path.dirname(DATA), "career_docs.jsonl")
        with open(src, encoding="utf-8") as f:
            docs = {
                d["content_hash"]: (d["title"], d["content"])
                for d in map(json.loads, f)
                if d
            }
        for q in load("queries.jsonl"):
            seen = []
            for s in systems:
                seen += [h for h in ranked(s, q["query"], args.k) if h not in seen]
            for h in seen:
                print(
                    json.dumps(
                        {
                            "qid": q["qid"],
                            "query": q["query"],
                            "content_hash": h,
                            "title": docs[h][0],
                            "content": docs[h][1],
                        },
                        ensure_ascii=False,
                    )
                )
    else:
        qrels = {}
        for r in load("qrels.jsonl"):
            qrels.setdefault(r["qid"], {})[r["content_hash"]] = r["grade"]
        queries = [q for q in load("queries.jsonl") if q["qid"] in qrels]
        print(
            f"queries judged: {len(queries)}  judgments: {sum(len(v) for v in qrels.values())}"
        )
        print(f"{'system':16} nDCG@10  R@10   S@3    MRR")
        for s in systems:
            nd = rc = s3 = mrr = 0.0
            for q in queries:
                g = qrels[q["qid"]]
                top = ranked(s, q["query"], 10)
                dcg = sum(
                    (2 ** g.get(h, 0) - 1) / math.log2(i + 2) for i, h in enumerate(top)
                )
                ideal = sorted(g.values(), reverse=True)[:10]
                idcg = (
                    sum((2**x - 1) / math.log2(i + 2) for i, x in enumerate(ideal)) or 1
                )
                nd += dcg / idcg
                rel = {h for h, x in g.items() if x >= 1}
                rc += len(rel & set(top)) / len(rel) if rel else 0
                s3 += any(g.get(h, 0) == 2 for h in top[:3])
                mrr += next(
                    (1 / (i + 1) for i, h in enumerate(top) if g.get(h, 0) >= 1), 0
                )
            n = len(queries) or 1
            print(f"{s:16} {nd / n:.3f}    {rc / n:.3f}  {s3 / n:.3f}  {mrr / n:.3f}")


if __name__ == "__main__":
    main()
