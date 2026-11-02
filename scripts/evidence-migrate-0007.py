#!/usr/bin/env python3
"""evidence-migrate-0007.py [--write]

One-off migration to ADR-0007's store (docs/adr/0007 § 9 step 1). DRY RUN by default: it reads
$JOBSEEK_DRIVE_DIR/evidence/, prints the counts and every blocking problem, and creates nothing.

  - mints doc_id = uuid.uuid7() for every document and drops the frozen ragdb integer `id`;
  - backfills created_at/updated_at where missing: the mtime of the earliest
    evidence/backups/career_docs*.jsonl holding that content_hash, else the migration time;
  - rewrites eval/qrels.jsonl from content_hash to doc_id (every row must map);
  - writes evidence/approved.jsonl, an empty pending.jsonl and rejected.jsonl, each validated by
    schemas/evidence-doc.schema.json (scripts/evidence-schema.py);
  - writes backups/doc_id-map-<stamp>.jsonl ({content_hash, doc_id}) for the Neon copy.

--write is refused while any document violates the schema or any qrels row is unmapped. It backs
career_docs.jsonl and qrels.jsonl up to evidence/backups/ first, and finally MOVES
career_docs.jsonl into backups/ (never deletes it). It refuses to run at all once approved.jsonl
exists: that file is the "already migrated" marker. Interrupted mid-write? Restore from the
backups named in the output, delete approved.jsonl, re-run.
"""

import argparse
import datetime
import glob
import importlib.util
import json
import os
import shutil
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "evidence_schema", os.path.join(HERE, "evidence-schema.py")
)
schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(schema)

# doc_id first, then the add-evidence field order; anything else keeps its place after these.
ORDER = [
    "doc_id",
    "content_hash",
    "title",
    "content",
    "doc_type",
    "source_class",
    "company",
    "repo",
    "period",
    "skills",
    "technologies",
    "domains",
    "author_confidence",
    "metadata",
    "created_at",
    "updated_at",
]


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def earliest_backup(backups):
    """content_hash -> (mtime, file) of the earliest career_docs backup that holds it."""
    seen = {}
    files = sorted(
        glob.glob(os.path.join(backups, "career_docs*.jsonl")), key=os.path.getmtime
    )
    for path in files:
        mtime = os.path.getmtime(path)
        for d in read_jsonl(path):
            h = d.get("content_hash")
            if h and h not in seen:
                seen[h] = (mtime, os.path.basename(path))
    return seen, files


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    data = os.environ.get("JOBSEEK_DRIVE_DIR") or sys.exit(
        "error: set JOBSEEK_DRIVE_DIR"
    )
    ev = os.path.join(data, "evidence")
    src = os.path.join(ev, "career_docs.jsonl")
    qrels_path = os.path.join(ev, "eval", "qrels.jsonl")
    backups = os.path.join(ev, "backups")
    state = {s: os.path.join(ev, f"{s}.jsonl") for s in ("approved", "pending", "rejected")}
    if os.path.exists(state["approved"]):
        sys.exit("error: evidence/approved.jsonl exists — already migrated; refusing to run")
    for s in ("pending", "rejected"):
        if os.path.exists(state[s]):
            sys.exit(f"error: evidence/{s}.jsonl exists without approved.jsonl — inspect by hand")
    if not os.path.exists(src):
        sys.exit("error: no evidence/career_docs.jsonl to migrate")

    now = datetime.datetime.now().astimezone()
    stamp = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    docs = read_jsonl(src)
    # Read the backups BEFORE writing one: copy2 keeps the source's mtime, so a fresh backup
    # would otherwise "contain" every hash and hand the no-history documents its mtime.
    first_seen, backup_files = earliest_backup(backups) if os.path.isdir(backups) else ({}, [])

    out, mapping, dropped_ids = [], {}, 0
    paths = {"kept": 0, "earliest-backup": 0, "migration-time": 0}
    for d in docs:
        d = dict(d)
        dropped_ids += d.pop("id", None) is not None
        d["doc_id"] = str(uuid.uuid7())
        if d.get("created_at") and d.get("updated_at"):
            paths["kept"] += 1
        else:
            hit = first_seen.get(d["content_hash"])
            if hit:
                ts = datetime.datetime.fromtimestamp(hit[0]).astimezone().isoformat()
                paths["earliest-backup"] += 1
            else:
                ts = now.isoformat()
                paths["migration-time"] += 1
            d.setdefault("created_at", ts)
            d.setdefault("updated_at", d["created_at"])
        mapping[d["content_hash"]] = d["doc_id"]
        out.append({**{k: d[k] for k in ORDER if k in d}, **{k: v for k, v in d.items() if k not in ORDER}})

    violations = [(n, d.get("title", "")[:60], m) for n, d in enumerate(out, 1) for m in schema.errors(d)]
    dup_hash = len(out) - len(mapping)
    dup_id = len(out) - len({d["doc_id"] for d in out})

    qrels = read_jsonl(qrels_path)
    new_qrels, unmapped = [], []
    for r in qrels:
        doc_id = mapping.get(r.get("content_hash"))
        if doc_id is None:
            unmapped.append(r)
            continue
        new_qrels.append({"qid": r["qid"], "doc_id": doc_id, "grade": r["grade"]})

    print(f"schema engine:             {schema.ENGINE}")
    print(f"backups scanned:           {len(backup_files)} ({', '.join(map(os.path.basename, backup_files)) or 'none'})")
    print(f"documents:                 {len(docs)}")
    print(f"doc_ids minted:            {len(out)} (UUIDv7)")
    print(f"timestamps kept:           {paths['kept']}")
    print(f"backfilled, from backup:   {paths['earliest-backup']}")
    print(f"backfilled, migration now: {paths['migration-time']} ({now.isoformat()})")
    print(f"old integer ids dropped:   {dropped_ids}")
    print(f"duplicate content_hash:    {dup_hash}")
    print(f"duplicate doc_id:          {dup_id}")
    print(f"qrels rows:                {len(qrels)}")
    print(f"qrels rows rewritten:      {len(new_qrels)}")
    print(f"qrels rows UNMAPPED:       {len(unmapped)}")
    for r in unmapped:
        print(f"  unmapped qid={r.get('qid')} grade={r.get('grade')} content_hash={str(r.get('content_hash'))[:16]}…")
    print(f"schema violations:         {len(violations)} (approved; pending and rejected are empty)")
    for n, title, m in violations:
        print(f"  approved line {n} ({title}): {m}")

    blocked = bool(violations or unmapped or dup_hash or dup_id)
    if not a.write:
        print(
            "dry run — nothing written."
            + (" --write is BLOCKED until every count above marked UNMAPPED/violations/duplicate is 0." if blocked else " Re-run with --write to apply."),
            file=sys.stderr,
        )
        return 1 if blocked else 0
    if blocked:
        sys.exit("error: refusing --write: fix the violations / unmapped qrels above first")

    os.makedirs(backups, exist_ok=True)
    shutil.copy2(src, os.path.join(backups, f"career_docs.migrate-0007-{stamp}.jsonl"))
    shutil.copy2(qrels_path, os.path.join(backups, f"qrels.migrate-0007-{stamp}.jsonl"))
    write_jsonl(
        os.path.join(backups, f"doc_id-map-{stamp}.jsonl"),
        [{"content_hash": h, "doc_id": i} for h, i in mapping.items()],
    )
    write_jsonl(state["approved"], out)  # the "already migrated" marker, so it goes first
    write_jsonl(state["pending"], [])
    write_jsonl(state["rejected"], [])
    write_jsonl(qrels_path, new_qrels)
    bad = [(s, n, m) for s, p in state.items() for n, m in schema.check_file(p)]
    if bad:
        for s, n, m in bad:
            print(f"{s}.jsonl:{n}: {m}", file=sys.stderr)
        sys.exit("error: written files fail validation — career_docs.jsonl left in place; restore from backups")
    os.replace(src, os.path.join(backups, f"career_docs.retired-0007-{stamp}.jsonl"))
    print(
        f"wrote approved.jsonl ({len(out)}), pending.jsonl (0), rejected.jsonl (0), qrels ({len(new_qrels)}); "
        f"backups/doc_id-map-{stamp}.jsonl; career_docs.jsonl moved to backups/career_docs.retired-0007-{stamp}.jsonl",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
