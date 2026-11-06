#!/usr/bin/env python3
"""pipeline-test.py — offline gate for prepare.py and assemble.py (ADR-0006 § 4j), fabricated data only.

Runs prepare.py twice over scripts/test/group-fixture.json (no network: --skip-discovery,
--offline) and asserts identical briefs; then fills drafts — every group a valid skip, one
invalid — and asserts assemble.py keeps the valid lines, reports the invalid one, records a
fingerprint that proposal-fingerprint.py reproduces, and moves a taken stamp by one minute.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
fx = json.load(open(os.path.join(HERE, "group-fixture.json")))


def run(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def prepared(root):
    for k in ("prs", "issues", "commits"):
        json.dump(fx[k], open(os.path.join(root, f"{k}.json"), "w"))
    json.dump(fx["gitlab"], open(os.path.join(root, "gitlab.json"), "w"))
    json.dump({"github_prs": len(fx["prs"]), "github_issues": len(fx["issues"]), "github_commits_raw": len(fx["commits"]), "errors": []}, open(os.path.join(root, "discovery.json"), "w"))  # fmt: skip
    run(os.path.join(SCRIPTS, "prepare.py"), "--since", "2026-01-01T00:00:00Z", "--until-at", "2026-01-11T00:00:00Z", "--out", root, "--gitlab", os.path.join(root, "gitlab.json"), "--skip-discovery", "--offline")  # fmt: skip


with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
    prepared(a)
    prepared(b)
    keys = [ln.split("\t") for ln in open(os.path.join(a, "groups.tsv")) if ln.strip()]
    assert keys and all(s.strip() for _, s in keys), (
        "every group needs a match sentence"
    )
    for k, _ in keys:
        ba, bb = (open(os.path.join(d, "briefs", k + ".json")).read() for d in (a, b))
        assert ba == bb, f"brief {k} differs between two runs"
        assert json.loads(ba)["items"], f"brief {k} has no items"

    for i, (k, _) in enumerate(keys):
        d = {"group": k, "action": "skip", "target_doc_id": None, "neighbours": [], "claim": "Fixture skip: nothing evidence-worthy.", "draft": None, "sources": [], "read": [], "numbers": [], "removed_in_verify": []}  # fmt: skip
        if i == 0:
            d["draft"] = {
                "reason": "a skip must carry draft: null"
            }  # invalid on purpose
        json.dump(d, open(os.path.join(a, "drafts", k + ".json"), "w"))
    out = json.loads(run(os.path.join(SCRIPTS, "assemble.py"), "--scratch", a, "--since", "2026-01-01T00:00:00Z", "--until-at", "2026-01-11T00:00:00Z", "--taken", "20260111T0000Z"))  # fmt: skip
    assert out["stamp"] == "20260111T0001Z", out
    run_file = json.load(open(out["run"]))
    assert run_file["proposals"]["skip"] == len(keys) - 1, run_file["proposals"]
    assert any(
        e.startswith(f"draft invalid: {keys[0][0]}") for e in run_file["errors"]
    ), run_file["errors"]
    spec = importlib.util.spec_from_file_location(
        "fp", os.path.join(SCRIPTS, "proposal-fingerprint.py")
    )
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    assert run_file["proposal_sha256"] == fp.fingerprint(out["proposal"])
    assert run_file["coverage"]["found"] == sum(
        len(fx[k]) for k in ("prs", "issues", "commits", "gitlab")
    )
    # --check: a `new` draft must satisfy the evidence-document schema itself, not a copy of it
    # (metadata.evidence as objects passed --check and failed intake, 2026-11-06).
    new = {"group": "acme-new", "action": "new", "target_doc_id": None, "neighbours": [], "claim": "Jane Doe built the Acme widget cache.", "sources": ["https://example.com/acme/widgets/pull/1"], "read": [], "numbers": [], "removed_in_verify": [],
           "draft": {"doc_id": "", "content_hash": "", "title": "Acme widget cache", "content": "Built the widget cache.", "doc_type": "contribution", "source_class": "personal-project", "company": "personal", "repo": "acme/widgets", "period": "2026-01", "skills": [], "technologies": [], "domains": [], "author_confidence": "operator", "metadata": {"evidence": ["https://example.com/acme/widgets/pull/1"]}}}  # fmt: skip
    chk = os.path.join(a, "check.json")
    json.dump(new, open(chk, "w"))
    assert run(os.path.join(SCRIPTS, "assemble.py"), "--check", chk).strip() == "valid"
    new["draft"]["metadata"]["evidence"] = [
        {"url": "https://example.com/acme/widgets/pull/1"}
    ]
    json.dump(new, open(chk, "w"))
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "assemble.py"), "--check", chk], capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 1 and "metadata/evidence/0" in r.stdout, r.stdout
# inbox-upload.py window: since = the largest until_at (not the newest file), a bare date gets
# T00:00:00Z, taken lists every stamp, no run files → 7 days back (ADR-0006 § 4l).
spec = importlib.util.spec_from_file_location(
    "iu", os.path.join(SCRIPTS, "inbox-upload.py")
)
iu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iu)
import datetime  # noqa: E402

now = datetime.datetime(2026, 1, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
runs = [{"folder": "processed", "name": "run-20260110T0000Z.json", "until_at": "2026-01-10T00:00:00Z"},
        {"folder": "drop", "name": "run-20260105T0000Z.json", "until_at": "2026-01-05T00:00:00Z"},
        {"folder": "inbox", "name": "run-20260111T0000Z.json", "until_at": None}]  # fmt: skip
iu.post = lambda url, payload: {"ok": True, "runs": runs}
w = iu.window("u", "t", [], now=now)
assert (
    w["since"] == "2026-01-10T00:00:00Z" and w["until_at"] == "2026-01-20T12:00:00Z"
), w
assert w["taken"] == "20260105T0000Z,20260110T0000Z,20260111T0000Z", w
w = iu.window("u", "t", ["--since", "2026-01-01", "--until", "2026-01-08"], now=now)
assert (w["since"], w["until_at"]) == (
    "2026-01-01T00:00:00Z",
    "2026-01-08T00:00:00Z",
), w
iu.post = lambda url, payload: {"ok": True, "runs": []}
assert iu.window("u", "t", [], now=now)["since"] == "2026-01-13T12:00:00Z"
print("pipeline-test: ok")
