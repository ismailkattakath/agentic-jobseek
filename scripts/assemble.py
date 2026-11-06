#!/usr/bin/env python3
"""assemble.py — build the proposal and run file from the drafters' output (ADR-0006 § 4j).

    assemble.py --scratch DIR --since ISO --until-at ISO [--taken STAMP,…] [--gitlab-error TEXT]
                [--note TEXT]… [--drafter NAME]
    assemble.py --check DRAFT.json      # one draft against the schema: prints `valid` or the errors

Reads DIR/groups.tsv (the groups prepare.py made), DIR/drafts/<key>.json (one drafter output
each), DIR/coverage.json and DIR/discovery.json. Every draft is checked against
schemas/proposal-line.schema.json with the repo's own validator; an invalid or missing draft is
listed in the run file's `errors` and left out — never repaired here. Lines are ordered by group
key, so the same drafts always give the same bytes.

The stamp is until_at as YYYYMMDDTHHMMZ; if --taken lists it (a run file with that stamp already
exists in the inbox or processed/), one minute is added until it is free.

Writes DIR/proposal-<stamp>.jsonl (absent when no draft is valid) and DIR/run-<stamp>.json, and
prints their paths — the routine uploads them with scripts/inbox-upload.py, run file last.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


schema_mod = _load("evidence_schema", "evidence-schema.py")
fingerprint = _load("proposal_fingerprint", "proposal-fingerprint.py").fingerprint
SCHEMA = json.load(
    open(
        os.path.join(HERE, "..", "schemas", "proposal-line.schema.json"),
        encoding="utf-8",
    )
)


def stamp_for(until_at, taken):
    t = datetime.datetime.strptime(until_at, "%Y-%m-%dT%H:%M:%SZ")
    t = t.replace(second=0)
    while (s := t.strftime("%Y%m%dT%H%MZ")) in taken:
        t += datetime.timedelta(minutes=1)
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check",
        metavar="DRAFT",
        help="validate one draft file and exit (drafters run this)",
    )
    ap.add_argument("--scratch")
    ap.add_argument("--since")
    ap.add_argument("--until-at")
    ap.add_argument("--taken", default="", help="comma-separated stamps already used")
    ap.add_argument(
        "--gitlab-error", help="the connector's exact error, if GitLab failed"
    )
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--drafter", default="evidence-draft workflow")
    a = ap.parse_args(argv)
    if a.check:
        try:
            bad = schema_mod.validate(
                SCHEMA, json.load(open(a.check, encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError) as e:
            bad = [f"unreadable: {e}"]
        print("valid" if not bad else "INVALID\n" + "\n".join(bad))
        return 1 if bad else 0
    if not (a.scratch and a.since and a.until_at):
        ap.error(
            "--scratch, --since and --until-at are required unless --check is given"
        )
    S = a.scratch

    keys = [
        ln.split("\t")[0] for ln in open(os.path.join(S, "groups.tsv")) if ln.strip()
    ]
    coverage = json.load(open(os.path.join(S, "coverage.json")))
    disc = json.load(open(os.path.join(S, "discovery.json")))
    errors, lines, counts = [], [], {"new": 0, "enrich": 0, "skip": 0}
    for key in sorted(keys):
        p = os.path.join(S, "drafts", key + ".json")
        if not os.path.exists(p):
            errors.append(f"draft missing: {key}")
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"draft not JSON: {key} ({e})")
            continue
        bad = schema_mod.validate(SCHEMA, d)
        if d.get("group") != key:
            bad.append(f"group is {d.get('group')!r}, expected {key!r}")
        if bad:
            errors.append(f"draft invalid: {key}: " + "; ".join(bad[:3]))
            continue
        counts[d["action"]] += 1
        lines.append(json.dumps(d, ensure_ascii=False, sort_keys=True))
    if a.gitlab_error:
        errors.append(f"GitLab: {a.gitlab_error}")
    errors += disc.get("errors", [])

    stamp = stamp_for(a.until_at, {x for x in a.taken.split(",") if x})
    run = {
        "since": a.since,
        "until_at": a.until_at,
        "github_prs": disc["github_prs"],
        "github_commits": coverage["kept_by_kind"].get(
            "commit", 0
        ),  # after merge/copy filters
        "github_issues": disc["github_issues"],
        "gitlab_commits": disc["gitlab_commits"],
        "groups": len(keys),
        "dropped_chores": coverage["chores"],
        "coverage": coverage,
        "proposals": counts,
        "drafter": a.drafter,
        "errors": errors,
        "notes": a.note,
    }
    out = {}
    if lines:
        pp = os.path.join(S, f"proposal-{stamp}.jsonl")
        with open(pp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        run["proposal_sha256"] = fingerprint(pp)
        run["proposal_bytes"] = os.path.getsize(pp)
        out["proposal"] = pp
    rp = os.path.join(S, f"run-{stamp}.json")
    json.dump(run, open(rp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    out["run"] = rp
    print(
        json.dumps({**out, "stamp": stamp, "proposals": counts, "errors": len(errors)})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
