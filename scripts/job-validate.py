#!/usr/bin/env python3
"""job-validate.py <job.json> [--items]

Validate a role folder's job.json against the JSON Resume job-description schema
(schemas/job-schema.json, pinned — see docs/adr/0003-job-schema.md), then check that the
extraction is usable for tailoring: a title and company, and at least one responsibility,
qualification and skill. Exit 1 on any failure.

  --items   after a pass, print one line per requirement to check against the evidence store:
            every responsibility, every qualification, and every skill as "name: keywords".
            scripts/job-evidence.sh reads this to run one evidence query per line.

Needs the `jsonschema` Python package (in the devShell). job.json is derived from posting.md,
which stays the source of truth; both are untrusted data.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(os.path.dirname(HERE), "schemas", "job-schema.json")


def items(job):
    for r in job.get("responsibilities", []):
        yield r.strip()
    for q in job.get("qualifications", []):
        yield q.strip()
    for s in job.get("skills", []):
        kw = ", ".join(s.get("keywords", []))
        yield f"{s.get('name', '').strip()}: {kw}" if kw else s.get("name", "").strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("job")
    ap.add_argument("--items", action="store_true")
    args = ap.parse_args(argv)

    try:
        import jsonschema
    except ImportError:
        print(
            "error: python package 'jsonschema' missing — run inside `nix develop`",
            file=sys.stderr,
        )
        return 1

    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    try:
        with open(args.job, encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.job}: {exc}", file=sys.stderr)
        return 1

    errors = sorted(
        jsonschema.Draft7Validator(schema).iter_errors(job), key=lambda e: list(e.path)
    )
    problems = [
        f"schema: {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors
    ]
    for key in ("title", "company"):
        if not str(job.get(key, "")).strip():
            problems.append(f"incomplete: {key} is empty")
    for key in ("responsibilities", "qualifications", "skills"):
        if not job.get(key):
            problems.append(f"incomplete: {key} is empty — extract it from posting.md")
    if "{{" in json.dumps(job):
        problems.append("incomplete: a {{PLACEHOLDER}} is left")

    if problems:
        for p in problems:
            print(f"FAIL  {p}", file=sys.stderr)
        return 1
    n = sum(1 for _ in items(job))
    print(f"PASS  {args.job}: schema-valid, {n} requirements", file=sys.stderr)
    if args.items:
        for line in items(job):
            if line:
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
