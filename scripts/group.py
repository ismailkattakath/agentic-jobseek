#!/usr/bin/env python3
"""group.py — deterministic grouping for the evidence-discover routine (step 4, ADR-0006 § 4i).

    group.py (--prs PRS.json --issues ISSUES.json --commits COMMITS.json [--gitlab GL.json] | --input ALL.json)
             --out DIR [--max-groups N] [--max-size 15] [--offline] [--cache DIR]

Inputs are the routine's step 2-3 results, as fetched: GitHub search items for merged PRs and
authored issues, `search/commits` items, and GitLab commits as [{project, sha, date, message,
files?}]. A JSON file may hold a list or a search page ({"items": [...]}).

The same input always gives the same groups. Each item gets a SUBSYSTEM from the paths it
changed (`gh api` file lists, cached; on a 403 — repository not attached — or with --offline it
falls back to the title's scope, then to "misc"). Items union on closing links (a PR that
closes an issue, a commit that names its PR) and on the same (repository, subsystem). A group
larger than --max-size splits by ISO week, then in date order. Thin one-item groups collect
into <repo>-misc. Groups rank by substance (characters of body and message); --max-groups cuts
the rest, which are counted, never silently dropped.

Writes DIR/groups/<key>.json ({key, items}), DIR/groups.tsv (key<TAB> — the routine writes the
sentence) and DIR/coverage.json, whose counts must sum to `found`.
"""

import argparse
import collections
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys

CHORE = re.compile(
    r"(?i)^(release(\(|:|\s)|chore\((deps|release|lock)|build\(deps|bump\b|merge (branch|pull)\b"
    r"|revert \"?release|v?\d+\.\d+\.\d+$)|\b(dependabot|renovate|lockfile|bump version)\b"
)
CC = re.compile(r"^(\w+)(?:\(([^)]+)\))?!?:\s")  # conventional commit: type(scope):
AREA_TITLE = re.compile(
    r"^([\w.@/-]+(?:,\s*[\w.@/-]+)*)(?::\s|$)"
)  # "claude, docs: …" / "plugins"
CLOSES = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:([\w.-]+/[\w.-]+))?#(\d+)"
)
SQUASH_REF = re.compile(r"\(#(\d+)\)\s*$")
EPIC = re.compile(r"\[(E\d+'?)\]")
COPY_REPO = re.compile(r"(?i)(backup|legacy|mirror|prerewrite|archive)")
THIN = 200


def load(path):
    if not path:
        return []
    data = json.load(open(path, encoding="utf-8"))
    return data.get("items", []) if isinstance(data, dict) else data


def repo_of(item):
    if "repository_url" in item:
        return item["repository_url"].split("/repos/")[1]
    return (item.get("repository") or {}).get("full_name", "")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


LOW_PREFIXES = (
    "docs/",
    "doc/",
    "tests/",
    "test/",
    ".claude/agent-memory/",
    ".claude/memory/",
)
LOW_FILES = {
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "flake.lock",
    "package-lock.json",
}


def repo_slug(repo):
    """A repository's name for group keys. GitLab keeps a "gl-" prefix: "gitlab:a/tools" and
    GitHub "b/tools" both used to become "tools", and one group overwrote the other (2026-11-06)."""
    name = repo.split("/")[-1]
    return slug(("gl-" + name) if repo.startswith("gitlab:") else name)


def put(groups, key, items):
    """Add items under key without ever overwriting: the same repository extends its group, a
    different repository that produced the same key gets a numbered key of its own."""
    repo = items[0]["repo"]
    k, n = key, 2
    while k in groups and groups[k][0]["repo"] != repo:
        k, n = f"{key}-r{n}", n + 1
    groups.setdefault(k, []).extend(items)


def area(path):
    """A path's subsystem: up to three levels (directories, then the file's stem).
    backend/infra/stacks/x.py -> backend/infra/stacks; hosts/macos.nix -> hosts/macos."""
    p = path.lstrip("/")
    if p.startswith(".github/workflows/"):
        return "ci"
    segs = p.split("/")
    if len(segs) == 1:
        return "root"
    dirs, stem = segs[:-1], os.path.splitext(segs[-1])[0]
    return "/".join((dirs + [stem])[:3]) if len(dirs) < 3 else "/".join(dirs[:3])


def low(path):
    p = path.lstrip("/")
    return p.startswith(LOW_PREFIXES) or p.split("/")[-1] in LOW_FILES


def subsystem_from_paths(paths):
    strong = collections.Counter(area(p) for p in paths if not low(p))
    pool = strong or collections.Counter(area(p) for p in paths)
    if not pool:
        return None
    return sorted(pool.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def subsystem_from_title(title):
    """`type(scope): …` → scope; `area, area: …` or a bare `area, area` title → the first area."""
    t = title.strip()
    m = CC.match(t)
    if m:
        return m.group(2).split(",")[0].strip().lower() if m.group(2) else None
    m = AREA_TITLE.match(t)
    if m:
        return m.group(1).split(",")[0].strip().lower()
    return None


class Files:
    """Changed-file lists via `gh api`, cached on disk so a re-run is identical and cheap."""

    def __init__(self, cache, offline):
        self.cache, self.offline = cache, offline
        if cache:
            os.makedirs(cache, exist_ok=True)

    def _path(self, key):
        return os.path.join(
            self.cache, hashlib.sha256(key.encode()).hexdigest()[:24] + ".json"
        )

    def get(self, key, endpoint, jq):
        if self.cache and os.path.exists(self._path(key)):
            return json.load(open(self._path(key)))
        if self.offline:
            return None
        r = subprocess.run(
            ["gh", "api", "--paginate", endpoint, "--jq", jq],
            capture_output=True,
            text=True,
        )
        files = (
            [ln for ln in r.stdout.splitlines() if ln] if r.returncode == 0 else None
        )
        if self.cache and files is not None:
            json.dump(files, open(self._path(key), "w"))
        return files


def normalise(prs, issues, commits, gitlab):
    items, dropped = [], collections.Counter()
    for p in prs:
        repo = repo_of(p)
        items.append({
            "kind": "pr", "id": f"{repo}#{p['number']}", "repo": repo, "number": p["number"],
            "title": p.get("title", ""), "url": p.get("html_url", ""),
            "date": (p.get("pull_request") or {}).get("merged_at") or p.get("closed_at") or "",
            "body": p.get("body") or "",
        })  # fmt: skip
    for i in issues:
        repo = repo_of(i)
        items.append({
            "kind": "issue", "id": f"{repo}#{i['number']}", "repo": repo, "number": i["number"],
            "title": i.get("title", ""), "url": i.get("html_url", ""),
            "date": i.get("created_at") or "", "state": i.get("state", ""),
            "labels": [x.get("name", "") for x in i.get("labels") or []], "body": i.get("body") or "",
        })  # fmt: skip
    seen_copy = {}
    commit_items = []
    for c in commits:
        cm = c.get("commit") or {}
        if (
            len(c.get("parents") or []) != 1
            or (cm.get("committer") or {}).get("name") == "GitHub"
        ):
            dropped["pr_merge_commits"] += 1
            continue
        msg = cm.get("message") or ""
        repo = repo_of(c)
        commit_items.append({
            "kind": "commit", "id": f"{repo}@{c['sha']}", "repo": repo, "sha": c["sha"],
            "title": msg.split("\n")[0], "url": c.get("html_url", ""),
            "date": (cm.get("author") or {}).get("date") or "", "body": msg,
        })  # fmt: skip
    # Copies: the same author date + subject in a backup/mirror repository. Keep the canonical one.
    rank = lambda it: (bool(COPY_REPO.search(it["repo"])), len(it["repo"]), it["repo"])  # noqa: E731
    for it in sorted(commit_items, key=rank):
        k = (it["date"], it["title"])
        if k in seen_copy:
            dropped["copies"] += 1
            seen_copy[k]["also_in"].append(it["repo"])
            continue
        it["also_in"] = []
        seen_copy[k] = it
        items.append(it)
    for g in gitlab:
        proj = g.get("project", "")
        msg = g.get("message") or g.get("title") or ""
        items.append({
            "kind": "gitlab_commit", "id": f"gitlab:{proj}@{g.get('sha', '')}",
            "repo": f"gitlab:{proj}", "sha": g.get("sha", ""), "title": msg.split("\n")[0],
            "url": g.get("url", ""), "date": g.get("date", ""), "body": msg,
            "persona": "alt", **({"files": g["files"]} if g.get("files") else {}),
            **({"diff": g["diff"]} if g.get("diff") else {}),
        })  # fmt: skip
    return items, dropped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prs")
    ap.add_argument("--issues")
    ap.add_argument("--commits")
    ap.add_argument("--gitlab")
    ap.add_argument(
        "--input", help="one JSON object with prs/issues/commits/gitlab lists"
    )
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-groups", type=int, default=100)
    ap.add_argument("--max-size", type=int, default=15)
    ap.add_argument(
        "--offline", action="store_true", help="never call gh; use item files or titles"
    )
    ap.add_argument(
        "--cache", help="directory for cached file lists (default OUT/.files)"
    )
    a = ap.parse_args(argv)

    if a.input:
        combined = json.load(open(a.input, encoding="utf-8"))
        raw = {k: combined.get(k, []) for k in ("prs", "issues", "commits", "gitlab")}
    else:
        raw = {k: load(getattr(a, k)) for k in ("prs", "issues", "commits", "gitlab")}
    found = sum(len(v) for v in raw.values())
    items, dropped = normalise(raw["prs"], raw["issues"], raw["commits"], raw["gitlab"])

    # Chores and commits that only restate a PR in this window.
    pr_ids = {it["id"] for it in items if it["kind"] == "pr"}
    kept = []
    for it in items:
        if CHORE.search(it["title"]):
            dropped["chores"] += 1
            continue
        m = SQUASH_REF.search(it["title"]) if it["kind"] == "commit" else None
        if m and f"{it['repo']}#{m.group(1)}" in pr_ids:
            dropped["duplicate_of_pr"] += 1
            continue
        kept.append(it)
    items = kept

    # Subsystem per item: changed paths first, then the title, then "misc".
    files = Files(a.cache or os.path.join(a.out, ".files"), a.offline)

    def paths(it):
        if it.get("files"):
            return it["files"]
        if it["kind"] == "pr":
            return files.get(
                it["id"],
                f"repos/{it['repo']}/pulls/{it['number']}/files?per_page=100",
                ".[].filename",
            )
        if it["kind"] == "commit":
            return files.get(
                it["id"], f"repos/{it['repo']}/commits/{it['sha']}", ".files[].filename"
            )
        return None

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        found_paths = dict(zip([it["id"] for it in items], ex.map(paths, items)))
    # Order of evidence for the subsystem (measured 2026-11-06 on a 286-PR window): a
    # conventional-commit SCOPE names the feature (dns, finops — none over 13 PRs), so it wins;
    # an issue's epic tag next; changed PATHS only when the title names no feature (bare-area
    # titles such as "claude, docs"); then the bare area; an issue with none keys on itself.
    for it in items:
        p = found_paths.get(it["id"])
        if p:
            it["files"] = p
        m = CC.match(it["title"].strip())
        scope = m.group(2).split(",")[0].strip().lower() if m and m.group(2) else None
        e = EPIC.search(it["title"]) if it["kind"] == "issue" else None
        if scope:
            sub, src = scope, "scope"
        elif e:
            sub, src = "spec-" + e.group(1).lower().replace("'", "p"), "epic"
        elif subsystem_from_paths(p or []):
            sub, src = subsystem_from_paths(p), "paths"
        elif subsystem_from_title(it["title"]):
            sub, src = subsystem_from_title(it["title"]), "area-title"
        else:
            # A shared fallback key would chain every unscoped issue, and every PR closing
            # one, into a single component (2026-11-06) — so it keys on itself.
            sub, src = (
                f"{it['kind']}-{it.get('number') or it.get('sha', '')[:8]}",
                "self",
            )
        it["subsystem"], it["subsystem_from"] = sub, src

    # Union-find: closing links, then the same (repo, subsystem).
    parent = {it["id"]: it["id"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = sorted((find(x), find(y)))
        parent[ry] = rx

    for it in items:
        if it["kind"] not in ("pr", "commit"):
            continue
        for m in CLOSES.finditer(it["body"]):
            target = f"{m.group(1) or it['repo']}#{m.group(2)}"
            if target in parent:
                union(it["id"], target)
    by_sub = collections.defaultdict(list)
    for it in items:
        by_sub[(it["repo"], it["subsystem"])].append(it["id"])
    for ids in by_sub.values():
        for x in ids[1:]:
            union(ids[0], x)

    comps = collections.defaultdict(list)
    for it in items:
        comps[find(it["id"])].append(it)

    # Name each component by its dominant (repo, subsystem); split large ones; pool thin ones.
    def name(group):
        c = collections.Counter((it["repo"], it["subsystem"]) for it in group)
        repo, sub = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return repo_slug(repo) + "-" + slug(sub)

    def week(it):
        d = it["date"][:10]
        if not d:
            return "w0"
        import datetime as _dt

        y, w, _ = _dt.date.fromisoformat(d).isocalendar()
        return f"{y}w{w:02d}"

    def coarse(it):
        # The first path level that is not low-signal, else the subsystem's first segment.
        for f in it.get("files") or []:
            if not low(f) and "/" in f:
                return f.split("/")[0].lstrip(".") or "root"
        return it["subsystem"].split("/")[0].split("-")[0]

    groups, misc = {}, collections.defaultdict(list)
    small = collections.defaultdict(list)
    for comp in comps.values():
        comp.sort(key=lambda it: (it["date"], it["id"]))
        if len(comp) == 1 and len(comp[0]["body"]) < THIN:
            misc[comp[0]["repo"]].append(comp[0])
            continue
        if (
            len(comp) <= 2
        ):  # a fragment: pooled with its repo's same-area fragments below
            small[(comp[0]["repo"], coarse(comp[0]))].extend(comp)
            continue
        base = name(comp)
        if len(comp) <= a.max_size:
            put(groups, base, comp)
            continue
        # Oversized: split by subsystem first (a link chain joined them), then by ISO week,
        # then in date order — each step deterministic.
        by_sub = collections.defaultdict(list)
        for it in comp:
            by_sub[(it["repo"], it["subsystem"])].append(it)
        for (repo, sub), part0 in sorted(by_sub.items()):
            sbase = repo_slug(repo) + "-" + slug(sub)
            if len(part0) <= a.max_size:
                put(groups, sbase, part0)
                continue
            by_week = collections.defaultdict(list)
            for it in part0:
                by_week[week(it)].append(it)
            for wk, chunk in sorted(by_week.items()):
                for i in range(0, len(chunk), a.max_size):
                    key = f"{sbase}-{wk}" + (
                        f"-{i // a.max_size + 1}" if len(chunk) > a.max_size else ""
                    )
                    put(groups, key, chunk[i : i + a.max_size])
    # Area pools still under 3 items join one per-repo pool: a lone fragment is not worth a
    # drafter of its own (~100K tokens, ADR-0006 § 4h.1).
    for repo, ar in sorted(k for k, v in small.items() if len(v) < 3):
        small[(repo, "other")].extend(small.pop((repo, ar)))
    for (repo, ar), its in sorted(small.items()):
        its.sort(key=lambda it: (it["date"], it["id"]))
        base = repo_slug(repo) + "-" + slug(ar) + "-small"
        for i in range(0, len(its), a.max_size):
            key = base + (f"-{i // a.max_size + 1}" if len(its) > a.max_size else "")
            put(groups, key, its[i : i + a.max_size])
    for repo, its in sorted(misc.items()):
        base = repo_slug(repo) + "-misc"
        for i in range(0, len(its), a.max_size):
            key = base + (f"-{i // a.max_size + 1}" if len(its) > a.max_size else "")
            put(groups, key, its[i : i + a.max_size])

    # A name collision across components (same dominant subsystem) gets a stable suffix.
    final = {}
    for key, its in sorted(groups.items()):
        k, n = key, 2
        while k in final:
            k, n = f"{key}-{n}", n + 1
        final[k] = sorted(its, key=lambda it: (it["date"], it["id"]))

    substance = lambda its: sum(len(it["body"]) for it in its)  # noqa: E731
    ranked = sorted(final.items(), key=lambda kv: (-substance(kv[1]), kv[0]))
    kept_groups, beyond = ranked[: a.max_groups], ranked[a.max_groups :]

    os.makedirs(os.path.join(a.out, "groups"), exist_ok=True)
    for key, its in kept_groups:
        json.dump({"key": key, "items": its}, open(os.path.join(a.out, "groups", key + ".json"), "w"), ensure_ascii=False, indent=1)  # fmt: skip
    with open(os.path.join(a.out, "groups.tsv"), "w") as f:
        f.writelines(f"{key}\t\n" for key, _ in kept_groups)

    grouped = sum(len(its) for _, its in kept_groups)
    beyond_n = sum(len(its) for _, its in beyond)
    coverage = {
        "found": found,
        "grouped": grouped,
        "chores": dropped["chores"],
        "filtered": {k: v for k, v in sorted(dropped.items()) if k != "chores"},
        "beyond_cap": beyond_n,
        "groups": len(kept_groups),
        "groups_beyond_cap": [k for k, _ in beyond],
        "largest_group": max((len(its) for _, its in kept_groups), default=0),
        "subsystem_from": dict(
            collections.Counter(it["subsystem_from"] for it in items)
        ),
        "kept_by_kind": dict(
            sorted(collections.Counter(it["kind"] for it in items).items())
        ),
    }
    total = grouped + dropped["chores"] + sum(coverage["filtered"].values()) + beyond_n
    json.dump(coverage, open(os.path.join(a.out, "coverage.json"), "w"), indent=1)
    if total != found:
        print(f"group.py: coverage does not sum ({total} != {found})", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                k: coverage[k]
                for k in (
                    "found",
                    "grouped",
                    "chores",
                    "beyond_cap",
                    "groups",
                    "largest_group",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
