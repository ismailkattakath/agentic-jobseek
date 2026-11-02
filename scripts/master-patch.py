#!/usr/bin/env python3
"""master-patch.py <patch.json> [--publish]

Apply a reviewed correction to the master résumé (the ONE copy, at $JOBSEEK_MASTER_URL —
docs/adr/0003 §3). Without --publish it is a dry run: fetch the current master (cache-busted),
apply every edit with exact-match assertions, bump meta.version's patch number, prepend the
reason to meta.notes, validate with `jsonresume validate`, and print a unified diff. With
--publish it does all of that and then updates the gist the URL points at (`gh api`), then
re-reads it to confirm the new version is live.

patch.json:
  {"reason": "one sentence: what the fact-check found",
   "edits": [
     {"op": "sub",    "path": "/work/0/highlights/2", "old": "exact text", "new": "replacement"},
     {"op": "remove", "path": "/skills/2/keywords/3", "expect": "exact value being removed"},
     {"op": "move",   "from": "/skills/1", "path": "/skills/0", "expect": {"name": "AI/ML Stack"}}
   ]}
Paths are JSON Pointers (RFC 6901). `sub` replaces one exact substring of a string field and
fails if it is absent or ambiguous; `remove` deletes an array item or key and fails unless its
current value equals `expect`; `replace` swaps a whole value (`value`) and fails unless the
current value equals `expect`; `move` (as in RFC 6902) reorders an item within one array and
fails unless every key in `expect` matches the item being moved. A patch that no longer matches the live master FAILS — re-derive
it, never force it.
"""

import argparse
import copy
import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

GIST = re.compile(
    r"^https://gist\.githubusercontent\.com/[^/]+/([0-9a-f]+)/raw/(?:[0-9a-f]+/)?([^/?#]+)"
)


def pointer(doc, path):
    """Return (parent, key) for a JSON Pointer."""
    parts = [
        p.replace("~1", "/").replace("~0", "~") for p in path.lstrip("/").split("/")
    ]
    node = doc
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    return node, (int(last) if isinstance(node, list) else last)


def fetch(url):
    sep = "&" if "?" in url else "?"
    with urllib.request.urlopen(f"{url}{sep}t={int(time.time())}", timeout=30) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def bump(version):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", str(version or ""))
    if not m:
        raise SystemExit(f"error: meta.version {version!r} is not MAJOR.MINOR.PATCH")
    return f"{m[1]}.{m[2]}.{int(m[3]) + 1}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("patch")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args(argv)

    url = os.environ.get("JOBSEEK_MASTER_URL") or sys.exit(
        "error: set JOBSEEK_MASTER_URL (see .envrc)"
    )
    patch = json.load(open(args.patch, encoding="utf-8"))
    reason, edits = patch.get("reason", "").strip(), patch.get("edits", [])
    if not reason or not edits:
        sys.exit("error: patch needs a non-empty reason and at least one edit")

    old = fetch(url)
    new = copy.deepcopy(old)
    # Removals run last and from the highest index down, so earlier paths stay valid.
    ordered = [e for e in edits if e.get("op") != "remove"] + sorted(
        (e for e in edits if e.get("op") == "remove"),
        key=lambda e: e["path"],
        reverse=True,
    )
    for e in ordered:
        parent, key = pointer(new, e["path"])
        if e.get("op") == "sub":
            cur = parent[key]
            if not isinstance(cur, str) or cur.count(e["old"]) != 1:
                sys.exit(
                    f"error: {e['path']}: 'old' text must occur exactly once (found {cur.count(e['old']) if isinstance(cur, str) else 'non-string'})"
                )
            parent[key] = cur.replace(e["old"], e["new"])
        elif e.get("op") == "remove":
            if parent[key] != e.get("expect"):
                sys.exit(
                    f"error: {e['path']}: value is {parent[key]!r}, expected {e.get('expect')!r}"
                )
            del parent[key]
        elif e.get("op") == "replace":
            if parent[key] != e.get("expect"):
                sys.exit(f"error: {e['path']}: current value differs from expect")
            parent[key] = e["value"]
        elif e.get("op") == "move":
            src, i = pointer(new, e["from"])
            if src is not parent or not isinstance(parent, list):
                sys.exit(
                    f"error: move {e['from']} -> {e['path']}: only within one array"
                )
            item, want = parent[i], e.get("expect") or {}
            if not want or any(item.get(k) != v for k, v in want.items()):
                sys.exit(f"error: {e['from']}: item does not match expect {want!r}")
            parent.insert(key, parent.pop(i))
        else:
            sys.exit(f"error: unknown op {e.get('op')!r}")

    meta = new.setdefault("meta", {})
    meta["version"] = bump(meta.get("version"))
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    meta["lastModified"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["notes"] = f"v{meta['version']} ({now:%Y-%m-%d}): {reason} " + meta.get(
        "notes", ""
    )

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(new, f, indent=2, ensure_ascii=False)
        f.write("\n")
        candidate = f.name
    v = subprocess.run(
        ["jsonresume", "validate", "--path", candidate], capture_output=True, text=True
    )
    if v.returncode != 0:
        sys.exit(
            f"error: candidate fails JSON Resume validation:\n{v.stdout}{v.stderr}"
        )

    a = json.dumps(old, indent=2, ensure_ascii=False).splitlines()
    b = json.dumps(new, indent=2, ensure_ascii=False).splitlines()
    sys.stdout.writelines(
        line + "\n"
        for line in difflib.unified_diff(
            a, b, "master (live)", f"master v{meta['version']}", lineterm="", n=1
        )
    )
    print(
        f"\n{len(edits)} edit(s); {old.get('meta', {}).get('version')} -> {meta['version']}; valid; candidate {candidate}",
        file=sys.stderr,
    )

    if not args.publish:
        print(
            "dry run — re-run with --publish after the operator approves",
            file=sys.stderr,
        )
        return 0
    m = GIST.match(url)
    if not m:
        sys.exit(
            "error: --publish supports a gist raw URL only; publish this candidate by hand"
        )
    gist_id, filename = m[1], m[2]
    body = json.dumps(
        {"files": {filename: {"content": open(candidate, encoding="utf-8").read()}}}
    )
    subprocess.run(
        ["gh", "api", "-X", "PATCH", f"gists/{gist_id}", "--input", "-"],
        input=body,
        text=True,
        check=True,
        capture_output=True,
    )
    live = json.loads(
        subprocess.run(
            ["gh", "api", f"gists/{gist_id}", "-q", f'.files["{filename}"].content'],
            text=True,
            check=True,
            capture_output=True,
        ).stdout
    )
    if live.get("meta", {}).get("version") != meta["version"]:
        sys.exit(
            f"error: published, but the gist reads version {live.get('meta', {}).get('version')}"
        )
    print(
        f"published master v{meta['version']} (raw URL may serve the old copy for a few minutes)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
