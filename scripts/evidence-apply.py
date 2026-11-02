#!/usr/bin/env python3
"""evidence-apply.py <proposal.jsonl> --approve all|GROUP,GROUP [--drop GROUP=TEXT ...] [--write]

The local half of the weekly evidence refresh (docs/adr/0006): merge the operator-approved
proposals from the cloud routine into the ONE source of truth, career_docs.jsonl in Drive.
Without --write it is a dry run that prints what each approved proposal would change.

  enrich  the target document (matched on its full content_hash) gains the proposal's sentences;
          its period end moves forward, its commit_refs grow, the proposal's metadata merges in,
          and metadata.enrichments records the group, the proposal file and the previous hash.
  new     a document in the add-evidence schema; missing fields get conservative defaults
          (doc_type contribution, the operator as author_confidence, empty skill lists).
  --drop  remove every added sentence of GROUP that contains TEXT (an edit made in review).

--write backs the JSONL up to evidence/backups/, rewrites it, moves the proposal and its run
file to evidence/inbox/applied/, and runs scripts/evidence-rebuild.sh so the cache follows.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "proposal_fingerprint", os.path.join(HERE, "proposal-fingerprint.py")
)
_fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fp)
fingerprint = _fp.fingerprint
TIERS = {
    "personal": "personal-project",
    "oss": "oss-contribution",
    "enterprise": "enterprise",
}
OPERATOR = os.environ.get("JOBSEEK_OPERATOR_HANDLE", "ismail")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extend_period(period, end):
    """Move a period's end forward to `end` (YYYY-MM); open-ended and free-text periods stay."""
    if not end or not period or "present" in period or not re.match(r"^\d{4}", period):
        return period
    start, _, last = period.partition("..")
    last = last or start
    return period if last >= end else f"{start}..{end}"


def merge_list(old, new):
    out = list(old or [])
    out += [x for x in new or [] if x not in out]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("proposal")
    ap.add_argument("--approve", required=True)
    ap.add_argument("--drop", action="append", default=[])
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    data = os.environ.get("JOBSEEK_DRIVE_DIR") or sys.exit(
        "error: set JOBSEEK_DRIVE_DIR"
    )
    src = os.path.join(data, "evidence", "career_docs.jsonl")
    # The routine records the content fingerprint of the proposal it COMPUTED
    # (scripts/proposal-fingerprint.py); the copy in Drive went through a connector the model
    # fills in, so a mismatch means a value changed on the way.
    run_file = os.path.join(
        os.path.dirname(a.proposal),
        os.path.basename(a.proposal)
        .replace("proposal-", "run-")
        .replace(".jsonl", ".json"),
    )
    want = (
        json.load(open(run_file, encoding="utf-8")) if os.path.exists(run_file) else {}
    ).get("proposal_sha256")
    have = fingerprint(a.proposal)
    if want and want != have:
        sys.exit(
            f"error: {os.path.basename(a.proposal)} is not the file the routine computed "
            f"(run file sha256 {want[:12]}…, this file {have[:12]}…) — re-run the routine, never apply a retyped copy"
        )
    if not want:
        print(
            "warning: the run file records no proposal_sha256 (written before 2026-11-02); integrity unchecked",
            file=sys.stderr,
        )
    proposals = [
        json.loads(line) for line in open(a.proposal, encoding="utf-8") if line.strip()
    ]
    wanted = (
        {p["group"] for p in proposals if p["action"] != "skip"}
        if a.approve == "all"
        else set(a.approve.split(","))
    )
    unknown = wanted - {p["group"] for p in proposals}
    if unknown:
        sys.exit(f"error: no such group(s): {sorted(unknown)}")
    drops = {}
    for d in a.drop:
        group, _, text = d.partition("=")
        drops.setdefault(group, []).append(text)

    lines = [json.loads(line) for line in open(src, encoding="utf-8") if line.strip()]
    index = {d["content_hash"]: i for i, d in enumerate(lines)}
    stamp = os.path.basename(a.proposal)
    tag = os.path.splitext(stamp)[0]
    today = datetime.date.today().isoformat()
    changes = []

    for p in proposals:
        if p["group"] not in wanted:
            continue
        draft = p.get("draft") or {}
        if p["action"] == "enrich":
            i = index.get(p.get("target_hash") or "")
            if i is None:
                sys.exit(
                    f"error: {p['group']}: target {p.get('target_hash')!r} is not in the source "
                    "(a full 64-character content_hash is required)"
                )
            doc = lines[i]
            # a drop names a clause; cut it out, keeping whatever else the sentence carries
            add = []
            for s in draft.get("add_sentences", []):
                for t in drops.get(p["group"], []):
                    s = s.replace(t, "")
                if s.strip(" .,"):
                    add.append(s.strip())
            old_hash = doc["content_hash"]
            doc["content"] = doc["content"].rstrip() + " " + " ".join(add)
            doc["content_hash"] = sha(doc["content"])
            doc["period"] = extend_period(
                doc.get("period", ""), draft.get("period_end")
            )
            meta = doc.setdefault("metadata", {})
            meta["commit_refs"] = merge_list(
                meta.get("commit_refs"), draft.get("commit_refs")
            )
            for k, v in (draft.get("metadata") or {}).items():
                meta.setdefault(k, v)
            meta.setdefault("enrichments", []).append(
                {
                    "on": today,
                    "proposal": stamp,
                    "group": p["group"],
                    "previous_hash": old_hash,
                    "sources": p.get("sources", []),
                }
            )
            changes.append(
                f"ENRICH {p['group']}: +{len(add)} sentence(s), period {doc['period']}, "
                f"hash {old_hash[:10]} -> {doc['content_hash'][:10]}"
            )
        elif p["action"] == "new":
            content = draft["content"]
            doc = {
                "title": draft["title"],
                "content": content,
                "content_hash": sha(content),
                "doc_type": draft.get("doc_type", "contribution"),
                "source_class": draft.get("source_class")
                or TIERS.get(draft.get("tier"), "personal-project"),
                "company": draft.get("company", ""),
                "repo": draft.get("repo", ""),
                "period": draft.get("period", today[:7]),
                "skills": draft.get("skills", []),
                "technologies": draft.get("technologies", []),
                "domains": draft.get("domains", []),
                "author_confidence": draft.get("author_confidence", OPERATOR),
                "metadata": {
                    **(draft.get("metadata") or {}),
                    "evidence": merge_list(
                        (draft.get("metadata") or {}).get("evidence"), p.get("sources")
                    ),
                    "origin": f"evidence-refresh {stamp}",
                },
            }
            if doc["metadata"].get("agent_assisted") not in (True, False):
                doc["metadata"]["agent_assisted"] = True
            if doc["content_hash"] in index:
                sys.exit(
                    f"error: {p['group']}: identical content already in the source"
                )
            lines.append(doc)
            index[doc["content_hash"]] = len(lines) - 1
            changes.append(
                f"NEW    {p['group']}: {doc['title'][:70]} ({doc['source_class']})"
            )

    print("\n".join(changes) or "nothing approved")
    if not a.write:
        print("dry run — re-run with --write to apply", file=sys.stderr)
        return
    backups = os.path.join(data, "evidence", "backups")
    os.makedirs(backups, exist_ok=True)
    shutil.copy2(src, os.path.join(backups, f"career_docs.{tag}.jsonl"))
    tmp = src + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for d in lines:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(tmp, src)
    # Graded judgments (evidence/eval/qrels.jsonl) are keyed on content_hash; an enriched document
    # keeps its grades (its content only grew), so follow it to its new hash.
    moved = {
        e["previous_hash"]: d["content_hash"]
        for d in lines
        for e in (d.get("metadata") or {}).get("enrichments", [])
        if e.get("proposal") == stamp
    }
    qrels = os.path.join(data, "evidence", "eval", "qrels.jsonl")
    if moved and os.path.exists(qrels):
        rows = [
            json.loads(line) for line in open(qrels, encoding="utf-8") if line.strip()
        ]
        hits = sum(r["content_hash"] in moved for r in rows)
        shutil.copy2(qrels, os.path.join(backups, f"qrels.{tag}.jsonl"))
        with open(qrels + ".tmp", "w", encoding="utf-8") as f:
            for r in rows:
                r["content_hash"] = moved.get(r["content_hash"], r["content_hash"])
                f.write(json.dumps(r) + "\n")
        os.replace(qrels + ".tmp", qrels)
        print(
            f"qrels: {hits} judgment(s) followed their document to its new hash",
            file=sys.stderr,
        )
    applied = os.path.join(os.path.dirname(a.proposal), "applied")
    os.makedirs(applied, exist_ok=True)
    run = os.path.join(
        os.path.dirname(a.proposal),
        stamp.replace("proposal-", "run-").replace(".jsonl", ".json"),
    )
    for f in (a.proposal, run):
        if os.path.exists(f):
            shutil.move(f, os.path.join(applied, os.path.basename(f)))
    print(
        f"wrote {len(lines)} documents; proposal moved to inbox/applied/",
        file=sys.stderr,
    )
    subprocess.run([os.path.join(HERE, "evidence-rebuild.sh")], check=True)


if __name__ == "__main__":
    main()
