#!/usr/bin/env python3
"""prepare.py — discovery, grouping, matching and drafter briefs in one command (ADR-0006 § 4j).

    prepare.py --since ISO --until-at ISO --out DIR [--gitlab GL.json] [--max-groups 100]
               [--match-python PY] [--sentences TSV] [--skip-discovery] [--offline]

Everything mechanical in the evidence-discover routine, in code, so two runs over the same
window do the same thing:
  1. DISCOVER — GitHub REST search for the author's merged PRs, direct commits and authored
     issues ($JOBSEEK_GITHUB_AUTHOR), paged to the end; a query whose total_count passes the
     API's 1,000-result cap is split in half by time until each part fits. 30 search calls a
     minute is the limit, so calls are spaced 2.2 s apart. Writes prs.json, commits.json,
     issues.json and discovery.json (counts, errors).
     GitLab too, when $JOBSEEK_GITLAB_TOKEN (a read_api token) is set — or, on the operator's
     Mac, through a logged-in `glab`: every member project's default-branch commits in the
     window (merges and deletion-scheduled projects skipped), each with its full message, the
     changed paths and the diff (capped at GITLAB_DIFF_CAP characters). Writes gitlab.json. A
     model cannot do this step: a connector's output reaches its context, never a file, so it
     could only write the diffs by retyping them (no run ever did — ADR-0006 § 4k).
  2. GROUP — scripts/group.py over those files and gitlab.json (or --gitlab, a file the caller
     already has).
  3. SENTENCE — one match sentence per group from its subsystem and top item titles, unless
     --sentences gives key<TAB>sentence lines (the model-written fallback).
  4. MATCH — scripts/match.py (fastembed in the cloud: --match-python /opt/fe/bin/python).
  5. NEIGHBOURS — the full text of every matched document, with scripts/sql/neighbours.sql.
  6. BRIEFS — briefs/<key>.json per group: the items, the three neighbours in full, the output
     path and schema. A drafter reads its brief once and nothing else.
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_GAP_S = 2.2
CC_PREFIX = re.compile(
    r"^(?:\w+(?:\([^)]*\))?!?|[\w.@/-]+(?:,\s*[\w.@/-]+)+):\s*"
)  # "fix(x): " or "a, b: "


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


class Search:
    def __init__(self):
        self.last = 0.0

    def page(self, kind, q, page):
        wait = SEARCH_GAP_S - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        for attempt in (1, 2):
            r = subprocess.run(
                ["gh", "api", f"search/{kind}?q={q}&per_page=100&page={page}"],
                capture_output=True,
                text=True,
            )
            self.last = time.monotonic()
            if r.returncode == 0:
                return json.loads(r.stdout)
            if "rate limit" in r.stderr.lower() and attempt == 1:
                time.sleep(61)
                continue
            raise RuntimeError(f"search/{kind} failed: {r.stderr.strip()[:200]}")

    def all(self, kind, qfmt, since, until):
        """Every item for qfmt (which takes {range}), splitting the window under the cap."""
        rng = f"{iso(since)}..{iso(until)}"
        first = self.page(kind, qfmt.format(range=rng), 1)
        if first["total_count"] > 1000 and (until - since).total_seconds() > 3600:
            mid = since + (until - since) / 2
            return self.all(kind, qfmt, since, mid) + self.all(kind, qfmt, mid, until)
        items, page = list(first["items"]), 1
        while len(items) < min(first["total_count"], 1000):
            page += 1
            got = self.page(kind, qfmt.format(range=rng), page)["items"]
            if not got:
                break
            items += got
        return items


GITLAB_API = "https://gitlab.com/api/v4/"
GITLAB_DIFF_CAP = (
    12000  # characters of diff per commit; the drafter reads it in its brief
)


class GitLab:
    """GitLab REST, read-only: a PRIVATE-TOKEN from the environment, else `glab api`."""

    def __init__(self):
        self.token = os.environ.get("JOBSEEK_GITLAB_TOKEN")
        if not self.token and not shutil.which("glab"):
            raise RuntimeError("JOBSEEK_GITLAB_TOKEN is unset (and no glab here)")

    def get(self, path):
        if not self.token:
            r = subprocess.run(["glab", "api", path], capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(
                    f"glab api {path.split('?')[0]}: {r.stderr.strip()[:200]}"
                )
            return json.loads(r.stdout)
        req = urllib.request.Request(
            GITLAB_API + path, headers={"PRIVATE-TOKEN": self.token}
        )
        for attempt in (1, 2, 3):
            try:
                with urllib.request.urlopen(req, timeout=60) as f:
                    return json.load(f)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    time.sleep(10 * attempt)
                    continue
                raise RuntimeError(
                    f"GitLab {path.split('?')[0]}: HTTP {e.code}"
                ) from None

    def all(self, path):
        out, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            got = self.get(f"{path}{sep}per_page=100&page={page}")
            out += got
            if len(got) < 100:
                return out
            page += 1


def gitlab_discover(since, until, out):
    """Every member project's default-branch commits in [since, until), with diffs → gitlab.json."""
    gl = GitLab()
    projects = [
        p for p in gl.all("projects?membership=true&archived=false&simple=true&order_by=id&sort=asc")
        if "-deletion_scheduled-" not in p["path_with_namespace"]
    ]  # fmt: skip
    rng = urllib.parse.urlencode({"since": iso(since), "until": iso(until)})
    commits = []
    for p in projects:
        for c in gl.all(f"projects/{p['id']}/repository/commits?{rng}"):
            if len(c.get("parent_ids") or []) > 1:
                continue  # a merge commit: its work is in the commits it merges
            files = gl.all(f"projects/{p['id']}/repository/commits/{c['id']}/diff")
            diff = "".join(f"--- {f['new_path']}\n{f.get('diff', '')}\n" for f in files)
            if len(diff) > GITLAB_DIFF_CAP:
                diff = (
                    diff[:GITLAB_DIFF_CAP]
                    + f"\n[diff cut at {GITLAB_DIFF_CAP} of {len(diff)} characters]"
                )
            commits.append({
                "project": p["path_with_namespace"], "sha": c["id"], "date": c["authored_date"],
                "message": c["message"].strip(), "url": c["web_url"],
                "files": sorted({f["new_path"] for f in files}), "diff": diff,
            })  # fmt: skip
    commits.sort(key=lambda c: (c["project"], c["date"], c["sha"]))
    path = os.path.join(out, "gitlab.json")
    json.dump(commits, open(path, "w"), indent=1, ensure_ascii=False)
    return path, len(projects)


def dedupe(items, key):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def discover(author, since, until, out):
    s, errors = Search(), []
    prs = dedupe(
        s.all(
            "issues", f"author:{author}+is:pr+is:merged+merged:{{range}}", since, until
        ),
        lambda x: x["url"],
    )
    issues = dedupe(
        s.all("issues", f"author:{author}+is:issue+created:{{range}}", since, until),
        lambda x: x["url"],
    )
    try:
        commits = dedupe(
            s.all("commits", f"author:{author}+committer-date:{{range}}", since, until),
            lambda x: x["url"],
        )
    except RuntimeError as e:
        commits, errors = [], [f"commit search: {e}"]
    for name, data in (("prs", prs), ("issues", issues), ("commits", commits)):
        json.dump(data, open(os.path.join(out, f"{name}.json"), "w"))
    return {"github_prs": len(prs), "github_issues": len(issues), "github_commits_raw": len(commits), "errors": errors}  # fmt: skip


AREA_ONLY = re.compile(
    r"^[\w.@/-]+(?:,\s*[\w.@/-]+)*$"
)  # "checks, docs" — names areas, not work
NOISE = re.compile(
    r"(?i)^(#+\s|closes|fixes|resolves|part of|refs?\b|co-authored-by|\s*[-*]\s*\[)"
)


def describe(it):
    """What an item did, in one line: its title, unless the title only lists areas — then the
    first substantive line of its body (heading, link and trailer lines skipped)."""
    t = CC_PREFIX.sub("", it["title"]).strip()
    if t and not AREA_ONLY.match(t):
        return t
    for ln in it["body"].splitlines()[1 if it["kind"] != "pr" else 0 :]:
        ln = re.sub(r"[*_`>]+", "", ln).strip()
        if len(ln) > 20 and not NOISE.match(ln):
            return ln[:160]
    return t


def sentence(group):
    """The subsystem and the three most substantial item titles, conventional prefixes removed."""
    items = sorted(group["items"], key=lambda it: (-len(it["body"]), it["id"]))
    subs = sorted({it["subsystem"] for it in items})
    titles = []
    for it in items:
        t = describe(it)
        if t and t not in titles:
            titles.append(t)
        if len(titles) == 3:
            break
    lead = subs[0].replace("/", " ").replace("-", " ")
    text = re.sub(
        r"https?://\S+", "", lead + ": " + "; ".join(titles)
    )  # URLs carry no meaning
    return re.sub(r"\s+", " ", text)[:300].strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True)
    ap.add_argument("--until-at", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gitlab")
    ap.add_argument("--max-groups", type=int, default=100)
    ap.add_argument("--match-python", default=sys.executable)
    ap.add_argument(
        "--sentences", help="key<TAB>sentence lines to use instead of the script's"
    )
    ap.add_argument(
        "--skip-discovery",
        action="store_true",
        help="reuse OUT/{prs,issues,commits}.json",
    )
    ap.add_argument(
        "--offline",
        action="store_true",
        help="no gh file lists, no match, no neighbours",
    )
    ap.add_argument(
        "--phase",
        choices=["all", "group", "match"],
        default="all",
        help="group: discover + group, then write titles.txt for the model's sentences; "
        "match: take --sentences, then match, neighbours and briefs",
    )
    a = ap.parse_args(argv)
    S = a.out
    os.makedirs(S, exist_ok=True)
    if a.phase == "match":
        if not a.sentences:
            sys.exit("--phase match needs --sentences")
        return finish(a, S, json.load(open(os.path.join(S, "discovery.json"))))

    # 1. Discover
    if a.skip_discovery:
        disc = json.load(open(os.path.join(S, "discovery.json")))
    else:
        author = os.environ.get("JOBSEEK_GITHUB_AUTHOR") or sys.exit(
            "set JOBSEEK_GITHUB_AUTHOR"
        )
        disc = discover(author, parse(a.since), parse(a.until_at), S)
    if not a.gitlab and not a.skip_discovery:
        try:
            a.gitlab, disc["gitlab_projects"] = gitlab_discover(
                parse(a.since), parse(a.until_at), S
            )
        except RuntimeError as e:
            disc["errors"].append(
                f"GitLab: {e}"
            )  # loud: the run file's errors, never a silent 0
    gl = json.load(open(a.gitlab)) if a.gitlab else []
    disc["gitlab_commits"] = len(gl)
    disc["gitlab_diffs"] = sum(1 for c in gl if c.get("diff"))
    json.dump(disc, open(os.path.join(S, "discovery.json"), "w"), indent=1)

    # 2. Group
    group = _load("group", "group.py")
    rc = group.main(
        ["--prs", os.path.join(S, "prs.json"), "--issues", os.path.join(S, "issues.json"),
         "--commits", os.path.join(S, "commits.json"), "--out", S, "--max-groups", str(a.max_groups)]
        + (["--gitlab", a.gitlab] if a.gitlab else []) + (["--offline"] if a.offline else [])
    )  # fmt: skip
    if rc:
        return rc
    if a.phase == "group":
        # The model writes each match sentence from these lines: scripted sentences found the
        # drafter's chosen target in 82.5 % of 120 enrich groups, the model's in 95.8 %
        # (2026-11-06), so the model keeps this one bounded step (ADR-0006 § 4j).
        keys = [
            ln.split("\t")[0]
            for ln in open(os.path.join(S, "groups.tsv"))
            if ln.strip()
        ]
        with open(os.path.join(S, "titles.txt"), "w") as f:
            for k in keys:
                g = json.load(open(os.path.join(S, "groups", k + ".json")))
                its = sorted(g["items"], key=lambda it: (-len(it["body"]), it["id"]))
                lines = []
                for it in its:
                    t = re.sub(r"https?://\S+", "", describe(it)).strip()
                    if t and t not in lines:
                        lines.append(t)
                f.write(f"{k}\t" + " | ".join(lines[:8])[:700] + "\n")
        print(
            json.dumps({"phase": "group", "groups": len(keys), "titles": "titles.txt"})
        )
        return 0
    return finish(a, S, disc)


def finish(a, S, disc):
    """Sentences, match, neighbours and briefs — phase "match", or the rest of phase "all"."""
    keys = [
        ln.split("\t")[0] for ln in open(os.path.join(S, "groups.tsv")) if ln.strip()
    ]
    groups = {k: json.load(open(os.path.join(S, "groups", k + ".json"))) for k in keys}

    # 3. Sentences
    given = {}
    if a.sentences:
        given = dict(
            ln.rstrip("\n").split("\t", 1) for ln in open(a.sentences) if "\t" in ln
        )
    with open(os.path.join(S, "groups.tsv"), "w") as f:
        for k in keys:
            f.write(f"{k}\t{given.get(k) or sentence(groups[k])}\n")

    # 4-5. Match and neighbours (skipped offline: the gate has no Neon)
    nb_by_key, docs = {k: [] for k in keys}, {}
    if not a.offline:
        with (
            open(os.path.join(S, "groups.tsv")) as fin,
            open(os.path.join(S, "matches.jsonl"), "w") as fout,
        ):
            r = subprocess.run([a.match_python, os.path.join(HERE, "match.py")], stdin=fin, stdout=fout, stderr=subprocess.PIPE, text=True)  # fmt: skip
        if r.returncode:
            sys.exit(f"match.py failed: {r.stderr.strip()[:300]}")
        for ln in open(os.path.join(S, "matches.jsonl")):
            m = json.loads(ln)
            nb_by_key[m["key"]] = [
                {
                    k: n[k]
                    for k in ("doc_id", "content_hash", "title", "cosine", "status")
                }
                for n in m["neighbours"]
            ]
        hashes = sorted({n["content_hash"] for v in nb_by_key.values() for n in v})
        if any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
            sys.exit("match.py returned a content_hash that is not 64 hex characters")
        if hashes:
            match = _load("match_mod", "match.py")
            sql = (
                open(os.path.join(HERE, "sql", "neighbours.sql"))
                .read()
                .replace(":hashes", "'{" + ",".join(hashes) + "}'")
            )
            for row in match.query(os.environ["JOBSEEK_NEON_SQL_HOST"], sql):
                docs[row["content_hash"]] = row
        with open(os.path.join(S, "neighbours.jsonl"), "w") as f:
            for h in hashes:
                f.write(json.dumps(docs[h], ensure_ascii=False) + "\n")

    # 6. Briefs
    os.makedirs(os.path.join(S, "briefs"), exist_ok=True)
    os.makedirs(os.path.join(S, "drafts"), exist_ok=True)
    sentences = dict(
        ln.rstrip("\n").split("\t", 1) for ln in open(os.path.join(S, "groups.tsv"))
    )
    for k in keys:
        brief = {
            "group": k,
            "window": {"since": a.since, "until_at": a.until_at},
            "match_sentence": sentences[k],
            "items": groups[k]["items"],
            "neighbours": nb_by_key[k],
            "neighbour_documents": [docs[n["content_hash"]] for n in nb_by_key[k] if n["content_hash"] in docs],
            "output_path": f"drafts/{k}.json",  # relative to the scratch directory
            "output_schema": "schemas/proposal-line.schema.json",
        }  # fmt: skip
        json.dump(brief, open(os.path.join(S, "briefs", k + ".json"), "w"), ensure_ascii=False, indent=1)  # fmt: skip
    cov = json.load(open(os.path.join(S, "coverage.json")))
    print(json.dumps({"groups": len(keys), "found": cov["found"], "grouped": cov["grouped"], "beyond_cap": cov["beyond_cap"], "neighbour_docs": len(docs)}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
