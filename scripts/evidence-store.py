#!/usr/bin/env python3
"""evidence-store.py — the ONE module that reads and writes the three evidence state files.

docs/adr/0007: $JOBSEEK_DRIVE_DIR/evidence/{approved,pending,rejected}.jsonl hold one schema
(schemas/evidence-doc.schema.json); a document's state IS the file it sits in. Every script that
touches them imports this module (the hyphenated name needs importlib, as evidence-review.py does
for proposal-fingerprint.py):

    spec = importlib.util.spec_from_file_location("evidence_store", HERE + "/evidence-store.py")
    store = importlib.util.module_from_spec(spec); spec.loader.exec_module(store)

Python API
    STATES                                  ("approved", "pending", "rejected")
    path(state) -> str
    load(state) -> (rows, sha256)           rows and sha come from the SAME bytes
    write(state, rows, expected_sha, reason="write") -> new sha256
                                            validate -> backup -> tmp -> re-hash -> os.replace;
                                            raises Conflict if the file changed since expected_sha
    move(key, from_state, to_state) -> dict ADR § 2.5; key = doc_id or content_hash (or an
                                            unambiguous prefix of 8+ chars), resolved in from_state.
                                            Rejecting APPENDS (rejected.jsonl is history, § 2.6)
    check_invariant(repair=False) -> dict   ADR § 4; {"ok", "problems", "repaired", "unrepairable"}
    intake(proposal_path, run_path=None, dry_run=False) -> dict    ADR § 3, one proposal file
    intake_inbox(dry_run=False) -> [dict]   every inbox/run-*.json whose proposal exists
    index_by_hash(rows) / index_by_doc_id(rows) -> dict
    Errors: StoreError > Conflict, InvariantError

CLI (what evidence-sync.sh calls; all human output goes to stderr)
    sync-prep --stage FILE --shas FILE [--dry-run]   intake-all + repair + dump, in that order
    intake-all [--dry-run]    repair [--check]    dump    sha    move KEY FROM TO
"""

import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


schema = _load_sibling("evidence_schema", "evidence-schema.py")
_fp = _load_sibling("proposal_fingerprint", "proposal-fingerprint.py")

STATES = ("approved", "pending", "rejected")
# ADR § 4: when one version sits in two files the most-decided copy survives.
DECIDED = {"approved": 0, "rejected": 1, "pending": 2}
# doc_id first, then the add-evidence field order; extras keep their place after these.
ORDER = [
    "doc_id", "content_hash", "title", "content", "doc_type", "source_class", "company",
    "repo", "period", "skills", "technologies", "domains", "author_confidence", "metadata",
    "created_at", "updated_at",
]  # fmt: skip
TIERS = {
    "personal": "personal-project",
    "oss": "oss-contribution",
    "enterprise": "enterprise",
}
OPERATOR = os.environ.get("JOBSEEK_OPERATOR_HANDLE", "ismail")


class StoreError(Exception):
    pass


class Conflict(StoreError):
    """A state file changed between read and replace (optimistic concurrency, HTTP If-Match)."""


class InvariantError(StoreError):
    pass


# --- primitives -----------------------------------------------------------------------------


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def root():
    data = os.environ.get("JOBSEEK_DRIVE_DIR")
    if not data:
        raise StoreError("set JOBSEEK_DRIVE_DIR to the job-search folder in Drive")
    return os.path.join(data, "evidence")


def path(state):
    if state not in STATES:
        raise StoreError(f"unknown state {state!r}; expected one of {STATES}")
    return os.path.join(root(), f"{state}.jsonl")


def normalize(doc):
    return {
        **{k: doc[k] for k in ORDER if k in doc},
        **{k: v for k, v in doc.items() if k not in ORDER},
    }


def dumps(rows):
    return "".join(json.dumps(normalize(r), ensure_ascii=False) + "\n" for r in rows)


def load(state):
    """(rows, sha256) of one state file. A missing file is empty. Rows and sha share one read."""
    p = path(state)
    raw = open(p, "rb").read() if os.path.exists(p) else b""
    rows = []
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise StoreError(f"{state}.jsonl:{n}: not JSON ({e})") from e
    return rows, hashlib.sha256(raw).hexdigest()


def index_by_hash(rows):
    return {r["content_hash"]: r for r in rows}


def index_by_doc_id(rows):
    return {r["doc_id"]: r for r in rows}


def _check_rows(state, rows):
    """Raise StoreError unless every row validates and the per-file uniqueness holds: content_hash
    in every file, doc_id in approved and pending only — rejected.jsonl is an append-only history
    with several rejected versions per doc_id (ADR § 2.6, § 4)."""
    bad = [
        f"{state}.jsonl row {n} ({str(d.get('title'))[:50]}): {m}"
        for n, d in enumerate(rows, 1)
        for m in schema.errors(d)
    ]
    for key in ("content_hash",) if state == "rejected" else ("doc_id", "content_hash"):
        seen = set()
        for d in rows:
            if d.get(key) in seen:
                bad.append(f"{state}.jsonl: duplicate {key} {str(d.get(key))[:16]}")
            seen.add(d.get(key))
    if bad:
        raise StoreError(
            "refusing to write an invalid file:\n  " + "\n  ".join(bad[:20])
        )


def write(state, rows, expected_sha, reason="write"):
    """Replace one state file. Aborts with Conflict if its sha256 is no longer `expected_sha`.

    Order: validate -> early sha check -> back up to evidence/backups/ -> write a private tmp ->
    re-hash the live file JUST before os.replace (the If-Match pattern) -> replace.
    A write that would not change a byte is a no-op (no backup, no replace).
    """
    _check_rows(state, rows)
    p = path(state)
    body = dumps(rows).encode("utf-8")
    current = open(p, "rb").read() if os.path.exists(p) else b""
    if hashlib.sha256(current).hexdigest() != expected_sha:
        raise Conflict(f"{state}.jsonl changed since it was read — re-run")
    if body == current:
        return expected_sha
    backups = os.path.join(root(), "backups")
    os.makedirs(backups, exist_ok=True)
    if current:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        shutil.copy2(p, os.path.join(backups, f"{state}.{stamp}.{reason}.jsonl"))
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    live = open(p, "rb").read() if os.path.exists(p) else b""
    if hashlib.sha256(live).hexdigest() != expected_sha:
        os.remove(tmp)
        raise Conflict(
            f"{state}.jsonl changed while it was being written — nothing replaced, re-run"
        )
    os.replace(tmp, p)
    return hashlib.sha256(body).hexdigest()


def _resolve(rows, key, state):
    key = key.strip().lower()
    hit = [r for r in rows if key in (r["doc_id"], r["content_hash"])]
    if not hit and len(key) >= 8:
        hit = [
            r
            for r in rows
            if r["doc_id"].startswith(key) or r["content_hash"].startswith(key)
        ]
    if len(hit) != 1:
        raise StoreError(
            f"{key!r} matches {len(hit)} documents in {state}.jsonl (need exactly 1)"
        )
    return hit[0]


# --- move (ADR § 2.5) -----------------------------------------------------------------------


def move(key, from_state, to_state):
    """Move one document between state files. The TARGET is written first, then the source, so a
    crash leaves a duplicate (repaired by check_invariant) and never a loss.

      -> approved, doc_id not in approved   approve-new     appended
      -> approved, doc_id in approved       approve-enrich  REPLACES that row (same doc_id); the
                                                            old content_hash leaves every file
      -> rejected                           APPENDED, never replacing: rejected.jsonl keeps every
                                            rejected version, several per doc_id (§ 2.6)
      -> pending                            appended, or replacing the row with the same doc_id
    Approval moves metadata.proposal into metadata.enrichments[] (the audit trail); a rejection
    keeps it (it records what was rejected).
    """
    if from_state == to_state:
        raise StoreError("from_state and to_state are the same")
    src, src_sha = load(from_state)
    dst, dst_sha = load(to_state)
    row = copy.deepcopy(_resolve(src, key, from_state))
    ts = now_iso()
    row["updated_at"] = ts
    clash = (
        None
        if to_state == "rejected"
        else next((r for r in dst if r["doc_id"] == row["doc_id"]), None)
    )
    if clash and clash["content_hash"] == row["content_hash"]:
        clash = None  # the same version already there: the source copy is just the stale half
    same = next((r for r in dst if r["content_hash"] == row["content_hash"]), None)
    previous = clash["content_hash"] if clash else None
    if to_state == "approved":
        meta = row.setdefault("metadata", {})
        proposal = meta.pop("proposal", None)
        if proposal:
            entry = {**proposal, "approved_at": ts}
            if previous:
                entry["previous_hash"] = previous
            meta.setdefault("enrichments", []).append(entry)
        if clash:
            row["created_at"] = clash["created_at"]
    kind = (
        ("approve-enrich" if clash else "approve-new")
        if to_state == "approved"
        else f"to-{to_state}"
    )
    if same:
        out = [row if r is same else r for r in dst]
    elif clash:
        out = [row if r is clash else r for r in dst]
    else:
        out = dst + [row]
    write(to_state, out, dst_sha, reason=f"move-{kind}")
    left = [r for r in src if r["content_hash"] != row["content_hash"]]
    write(from_state, left, src_sha, reason=f"move-{kind}")
    return {
        "kind": kind, "doc_id": row["doc_id"], "content_hash": row["content_hash"],
        "title": row.get("title"), "replaced_hash": previous, "from": from_state, "to": to_state,
    }  # fmt: skip


# --- invariant (ADR § 4) --------------------------------------------------------------------


def check_invariant(repair=False):
    """Each content_hash lives in exactly one state file; in approved and pending each doc_id
    appears at most once (rejected.jsonl may hold several versions of one doc_id, § 4).

    Repairable (and repaired when repair=True): the same version in two files -> keep the
    most-decided copy (approved > rejected > pending); the same version twice in one file.
    Unrepairable, reported not touched: schema violations and two DIFFERENT versions of one
    doc_id in approved or pending — those need a human.
    """
    loaded = {s: load(s) for s in STATES}
    problems, unrepairable, drop = [], [], {s: set() for s in STATES}
    for s, (rows, _) in loaded.items():
        for n, d in enumerate(rows, 1):
            for m in schema.errors(d):
                unrepairable.append(
                    f"{s}.jsonl row {n} ({str(d.get('title'))[:50]}): {m}"
                )
        by_id = {}
        for d in rows:
            by_id.setdefault(d.get("doc_id"), []).append(d)
        for doc_id, group in by_id.items():
            if s != "rejected" and len({g.get("content_hash") for g in group}) > 1:
                unrepairable.append(
                    f"{s}.jsonl: doc_id {doc_id} holds {len(group)} different versions"
                )
    seen = {}  # content_hash -> state that keeps it
    for s in sorted(STATES, key=DECIDED.get):
        seen_here = set()
        for d in loaded[s][0]:
            h = d.get("content_hash")
            if h in seen_here:
                problems.append(f"{s}.jsonl: version {str(h)[:12]} listed twice")
                drop[s].add(("dup", h))
            elif h in seen:
                problems.append(
                    f"version {str(h)[:12]} is in {seen[h]} AND {s} — keep {seen[h]}"
                )
                drop[s].add(("all", h))
            else:
                seen[h] = s
            seen_here.add(h)
    repaired = []
    if repair and not unrepairable:
        for s in STATES:
            if not drop[s]:
                continue
            rows, file_sha = loaded[s]
            kept, emitted = [], set()
            for d in rows:
                h = d["content_hash"]
                if ("all", h) in drop[s] or h in emitted:
                    continue
                emitted.add(h)
                kept.append(d)
            write(s, kept, file_sha, reason="repair")
            repaired.append(
                f"{s}.jsonl: removed {len(rows) - len(kept)} stale copy/copies"
            )
    return {
        "ok": not unrepairable and (not problems or bool(repaired)),
        "problems": problems, "repaired": repaired, "unrepairable": unrepairable,
    }  # fmt: skip


# --- intake (ADR § 3) -----------------------------------------------------------------------


def _extend_period(period, end):
    """Move a period's end forward to `end` (YYYY-MM); open-ended and free-text periods stay."""
    if not end or not period or "present" in period or not re.match(r"^\d{4}", period):
        return period
    start, _, last = period.partition("..")
    last = last or start
    return period if last >= end else f"{start}..{end}"


def _union(old, new):
    out = list(old or [])
    keys = {json.dumps(x, sort_keys=True) for x in out}
    for x in new or []:
        k = json.dumps(x, sort_keys=True)
        if k not in keys:
            out.append(x)
            keys.add(k)
    return out


def _stamp(proposal_path):
    return re.sub(r"^proposal-|\.jsonl$", "", os.path.basename(proposal_path))


def _proposal_meta(kind, stamp, p, **extra):
    return {
        "kind": kind, "stamp": stamp, "group": p.get("group", ""),
        "sources": list(p.get("sources") or []), "read": list(p.get("read") or []),
        "numbers": list(p.get("numbers") or []), "removed_in_verify": list(p.get("removed_in_verify") or []),
        **extra,
    }  # fmt: skip


def _archive(files, bucket, reason=None):
    """Move inbox files to inbox/<bucket>/ ('processed' or 'superseded'); superseded gets a reason."""
    dest = os.path.join(root(), "inbox", bucket)
    os.makedirs(dest, exist_ok=True)
    moved = []
    for f in files:
        if not f or not os.path.exists(f):
            continue
        target = os.path.join(dest, os.path.basename(f))
        if os.path.exists(target):
            target += "." + datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
        shutil.move(f, target)
        moved.append(target)
    if reason and moved:
        with open(
            os.path.join(dest, os.path.basename(files[0]) + ".reason.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(reason + "\n")
    return moved


def _new_doc(p, draft, stamp, ts):
    content = draft["content"]
    meta = dict(draft.get("metadata") or {})
    meta["evidence"] = _union(meta.get("evidence"), p.get("sources"))
    meta["origin"] = f"evidence-discover {stamp}"
    if meta.get("agent_assisted") not in (True, False):
        meta["agent_assisted"] = True
    meta["proposal"] = _proposal_meta("new", stamp, p)
    if not hasattr(uuid, "uuid7"):
        raise StoreError(
            "uuid.uuid7() needs Python >= 3.14 (RFC 9562); found "
            + sys.version.split()[0]
        )
    return normalize({
        "doc_id": str(uuid.uuid7()),
        "content_hash": sha(content),
        "title": draft["title"],
        "content": content,
        "doc_type": draft.get("doc_type", "contribution"),
        "source_class": draft.get("source_class") or TIERS.get(draft.get("tier"), "personal-project"),
        "company": draft.get("company", ""),
        "repo": draft.get("repo", ""),
        "period": draft.get("period", ts[:7]),
        "skills": draft.get("skills", []),
        "technologies": draft.get("technologies", []),
        "domains": draft.get("domains", []),
        "author_confidence": draft.get("author_confidence", OPERATOR),
        "metadata": meta,
        "created_at": ts,
        "updated_at": ts,
    })  # fmt: skip


def _norm(text):
    """Whitespace-collapsed, case-folded text: the comparison form for 'was this rejected?'."""
    return " ".join((text or "").split()).casefold()


def _rejected_text(rejected_by_doc, doc_id):
    """The normalized content of EVERY rejected version of one doc_id (§ 2.6)."""
    return [_norm(r["content"]) for r in rejected_by_doc.get(doc_id, [])]


def _coerce_draft(p, action):
    """The routine's draft, in the one shape intake reads. An enrichment's canonical draft is
    {"add_sentences": [...], "period_end": "...", "commit_refs": [...]} (routines/evidence-discover.md
    § Draft); a routine run that wrote `draft` as a plain string, with period_end / commit_refs at
    the proposal's top level, is accepted too (seen 2026-11-02, proposal-20261102T1916Z) — the
    string is split into sentences. Anything else is returned unchanged for the caller to refuse."""
    draft = p.get("draft")
    if action != "enrich" or isinstance(draft, dict):
        return draft
    if isinstance(draft, list):
        sentences = [str(x).strip() for x in draft if str(x).strip()]
    elif isinstance(draft, str) and draft.strip():
        sentences = [
            x.strip()
            for x in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`(])", draft.strip())
            if x.strip()
        ]
    else:
        return draft
    return {
        "add_sentences": sentences,
        "period_end": p.get("period_end"),
        "commit_refs": p.get("commit_refs") or [],
    }


def _sentences(draft, base_content, rejected=()):
    """(add, dropped): the draft's sentences not already in `base_content`, minus the ones any
    rejected version already contained (normalized); `dropped` lists those, for the report."""
    add, dropped = [], []
    for s in draft.get("add_sentences") or []:
        s = s.strip()
        if not s or s in base_content or s in add or s in dropped:
            continue
        (dropped if any(_norm(s) in r for r in rejected) else add).append(s)
    return add, dropped


def _read_parts(inbox, parts):
    """A run file's `proposal_parts` -> (paths, proposals) or raise StoreError. Each part carries
    its own content fingerprint, because the routine uploads each part in its own connector call
    (one call cannot carry a large proposal verbatim; ADR-0006 § 4g)."""
    paths, proposals = [], []
    for i, part in enumerate(parts):
        name = part.get("file", "") if isinstance(part, dict) else ""
        if not re.fullmatch(r"proposal-[0-9A-Za-z]+\.p[0-9]+\.jsonl", name):
            raise StoreError(f"proposal_parts[{i}]: bad file name {name!r}")
        path = os.path.join(inbox, name)
        have = _fp.fingerprint(path)
        if part.get("sha256") != have:
            raise StoreError(
                f"fingerprint mismatch in {name}: run file {str(part.get('sha256'))[:12]}…, "
                f"part {have[:12]}… — not the file the routine computed (ADR-0006 § 4d)"
            )
        paths.append(path)
        proposals += [
            json.loads(line) for line in open(path, encoding="utf-8") if line.strip()
        ]
    return paths, proposals


def intake(proposal_path, run_path=None, dry_run=False):
    """Take one routine proposal file into pending.jsonl (ADR § 3). Returns a report dict whose
    "status" is processed | superseded | error | planned (dry_run). All-or-nothing: if any row
    cannot be applied nothing is written and the files stay in the inbox.

    Idempotent: a re-run after a crash between the pending write and the file move finds every
    version already present (new -> duplicate hash) or every sentence already added (enrich), so
    it changes nothing and just moves the files.
    """
    if run_path is None:
        run_path = os.path.join(
            os.path.dirname(proposal_path),
            os.path.basename(proposal_path)
            .replace("proposal-", "run-", 1)
            .replace(".jsonl", ".json"),
        )
    stamp = _stamp(proposal_path)
    rep = {
        "proposal": os.path.basename(proposal_path),
        "status": "processed",
        "rows": [],
        "errors": [],
        "warnings": [],
    }
    files = [proposal_path, run_path]

    run = {}
    if os.path.exists(run_path):
        try:
            run = json.load(open(run_path, encoding="utf-8"))
        except json.JSONDecodeError as e:
            run = {}
            rep["warnings"].append(f"run file is not JSON ({e})")
    want = run.get("proposal_sha256")
    if run.get("proposal_parts"):
        # proposal_path is then only the stem the stamp comes from; the parts are the data.
        try:
            part_paths, proposals = _read_parts(
                os.path.dirname(run_path), run["proposal_parts"]
            )
            have = want = None
            files = part_paths + [run_path]
            rep["proposal"] = ", ".join(os.path.basename(f) for f in part_paths)
        except (StoreError, json.JSONDecodeError, OSError) as e:
            have, proposals = None, None
            rep["reason"] = f"proposal parts unusable ({e})"
            files = [
                os.path.join(os.path.dirname(run_path), x.get("file", ""))
                for x in run["proposal_parts"]
                if isinstance(x, dict)
            ] + [run_path]
    else:
        try:
            have = _fp.fingerprint(proposal_path)
            proposals = [
                json.loads(line)
                for line in open(proposal_path, encoding="utf-8")
                if line.strip()
            ]
        except (json.JSONDecodeError, OSError) as e:
            have, proposals = None, None
            rep["reason"] = f"proposal is not readable JSONL ({e})"
    if proposals is not None and want and want != have:
        rep["reason"] = (
            f"fingerprint mismatch: run file {want[:12]}…, proposal {have[:12]}… — the file is not the "
            "one the routine computed (ADR-0006 § 4d)"
        )
    if "reason" in rep:
        rep["status"] = "superseded"
        if not dry_run:
            _archive(files, "superseded", rep["reason"])
        return rep
    if not want and not run.get("proposal_parts"):
        rep["warnings"].append(
            "run file records no proposal_sha256; integrity unchecked"
        )

    approved, _ = load("approved")
    pending, pending_sha = load("pending")
    rejected, _ = load("rejected")
    hashes = {r["content_hash"] for r in approved + pending + rejected}
    # LEGACY: resolves a pre-0007 proposal's target_hash to a doc_id (see the enrich branch below).
    hash_to_doc = {
        r["content_hash"]: r["doc_id"] for r in approved + pending + rejected
    }
    for r in (
        approved + pending + rejected
    ):  # LEGACY: a pre-0007 target_hash may name a superseded version
        for e in (r.get("metadata") or {}).get("enrichments", []):
            if e.get("previous_hash"):
                hash_to_doc.setdefault(e["previous_hash"], r["doc_id"])
    appr_by_doc = index_by_doc_id(approved)
    rej_by_doc = {}  # doc_id -> EVERY rejected version (history, not one row)
    for r in rejected:
        rej_by_doc.setdefault(r["doc_id"], []).append(r)
    rej_content = {_norm(r["content"]): r["doc_id"] for r in rejected}
    pend = list(pending)
    ts = now_iso()
    changed = False

    for p in proposals:
        group, action = p.get("group", "?"), p.get("action")
        out = {"group": group, "action": action}
        rep["rows"].append(out)
        draft = _coerce_draft(p, action)
        if action == "skip":
            out["outcome"] = "dropped"
            continue
        if action not in ("new", "enrich") or not isinstance(draft, dict):
            out["outcome"] = "error"
            rep["errors"].append(f"{group}: action {action!r} needs a draft object")
            continue
        try:
            if action == "new":
                doc = _new_doc(p, draft, stamp, ts)
                if doc["content_hash"] in hashes:
                    out["outcome"] = (
                        "duplicate (identical content already in a state file)"
                    )
                    continue
                if (
                    _norm(doc["content"]) in rej_content
                ):  # § 2.6: a rejected item is never asked about again
                    out.update(
                        outcome="dropped (content matches a rejected document)",
                        dropped_as_rejected=[
                            f"{doc['title']} (rejected doc_id {rej_content[_norm(doc['content'])]})"
                        ],
                    )
                    continue
                pend.append(doc)
                hashes.add(doc["content_hash"])
                out.update(
                    outcome="pending: new document",
                    doc_id=doc["doc_id"],
                    content_hash=doc["content_hash"],
                )
                changed = True
                continue
            # enrich: contract = target_doc_id. LEGACY: the pre-0007 routine sent target_hash;
            # kept only for proposals written before the cloud routine was renamed (2026-11-02).
            # Drop the target_hash fallback (and the previous_hash mapping above) once none remain.
            tid = p.get("target_doc_id") or hash_to_doc.get(p.get("target_hash") or "")
            if not tid:
                raise StoreError(
                    f"target {p.get('target_doc_id') or p.get('target_hash')!r} is in no state file"
                )
            pend_by_doc = index_by_doc_id(pend)
            if tid in pend_by_doc:  # § 3.5 — merge into the pending version in place
                cur = pend_by_doc[tid]
                add, dropped = _sentences(
                    draft, cur["content"], _rejected_text(rej_by_doc, tid)
                )
                if dropped:
                    out["dropped_as_rejected"] = dropped
                if not add:
                    out["outcome"] = (
                        "skipped (all sentences previously rejected)"
                        if dropped
                        else "no-op (nothing new to add to the pending version)"
                    )
                    continue
                new = copy.deepcopy(cur)
                new["content"] = cur["content"].rstrip() + " " + " ".join(add)
                new["content_hash"] = sha(new["content"])
                new["period"] = _extend_period(
                    new.get("period", ""), draft.get("period_end")
                )
                meta = new.setdefault("metadata", {})
                meta["commit_refs"] = _union(
                    meta.get("commit_refs"), draft.get("commit_refs")
                )
                for k, v in (draft.get("metadata") or {}).items():
                    meta.setdefault(k, v)
                prop = meta.get("proposal") or _proposal_meta("enrich", stamp, p)
                prop["sources"] = _union(prop.get("sources"), p.get("sources"))
                prop["read"] = _union(prop.get("read"), p.get("read"))
                prop["numbers"] = _union(prop.get("numbers"), p.get("numbers"))
                prop["removed_in_verify"] = _union(
                    prop.get("removed_in_verify"), p.get("removed_in_verify")
                )
                prop["merged"] = prop.get("merged", []) + [
                    {"stamp": stamp, "group": group, "added_sentences": add}
                ]
                if dropped:
                    prop["dropped_as_rejected"] = _union(
                        prop.get("dropped_as_rejected"), dropped
                    )
                meta["proposal"] = prop
                new["updated_at"] = ts
                if new["content_hash"] in hashes:
                    out["outcome"] = (
                        "duplicate (merged content already in a state file)"
                    )
                    continue
                pend = [new if r is cur else r for r in pend]
                hashes.add(new["content_hash"])
                out.update(
                    outcome=f"pending: merged +{len(add)} sentence(s)",
                    doc_id=tid,
                    content_hash=new["content_hash"],
                )
                changed = True
            elif tid in appr_by_doc:  # § 3.4 — a new version of an approved document
                base = appr_by_doc[tid]
                add, dropped = _sentences(
                    draft, base["content"], _rejected_text(rej_by_doc, tid)
                )
                if dropped:
                    out["dropped_as_rejected"] = dropped
                if not add:
                    out["outcome"] = (
                        "skipped (all sentences previously rejected)"
                        if dropped
                        else "no-op (nothing new relative to the approved version)"
                    )
                    continue
                new = copy.deepcopy(base)
                new["content"] = base["content"].rstrip() + " " + " ".join(add)
                new["content_hash"] = sha(new["content"])
                new["period"] = _extend_period(
                    new.get("period", ""), draft.get("period_end")
                )
                meta = new.setdefault("metadata", {})
                meta.pop("proposal", None)
                meta["commit_refs"] = _union(
                    meta.get("commit_refs"), draft.get("commit_refs")
                )
                for k, v in (draft.get("metadata") or {}).items():
                    meta.setdefault(k, v)
                meta["proposal"] = _proposal_meta(
                    "enrich",
                    stamp,
                    p,
                    base_hash=base["content_hash"],
                    added_sentences=add,
                    **({"dropped_as_rejected": dropped} if dropped else {}),
                )
                new["updated_at"] = ts
                if new["content_hash"] in hashes:
                    out["outcome"] = (
                        "duplicate (this version already exists in a state file)"
                    )
                    continue
                pend.append(new)
                hashes.add(new["content_hash"])
                out.update(
                    outcome=f"pending: enrichment +{len(add)} sentence(s)",
                    doc_id=tid,
                    content_hash=new["content_hash"],
                )
                changed = True
            elif tid in rej_by_doc:
                out["outcome"] = "dropped (target document was rejected)"
            else:
                raise StoreError(f"target doc_id {tid} is in no state file")
        except (StoreError, KeyError) as e:
            out["outcome"] = "error"
            rep["errors"].append(
                f"{group}: {e!r}" if isinstance(e, KeyError) else f"{group}: {e}"
            )

    if not rep["errors"] and changed:
        try:
            _check_rows("pending", pend)
        except StoreError as e:
            rep["errors"].append(str(e))
    if rep["errors"]:
        rep["status"] = (
            "error"  # files stay in the inbox; fix the proposal or the code, re-run
        )
        return rep
    if dry_run:
        rep["status"] = "planned"
        return rep
    if changed:
        write("pending", pend, pending_sha, reason=f"intake-{stamp}")
    rep["moved"] = [os.path.basename(f) for f in _archive(files, "processed")]
    return rep


def intake_inbox(dry_run=False):
    """intake() for every inbox/run-*.json whose proposal-*.jsonl sits beside it. A proposal with
    no run file yet is skipped: the routine writes the run file LAST, so it is still mid-write."""
    inbox = os.path.join(root(), "inbox")
    reports = []
    if not os.path.isdir(inbox):
        return reports
    for name in sorted(os.listdir(inbox)):
        if not (name.startswith("run-") and name.endswith(".json")):
            continue
        stem = name[len("run-") : -len(".json")]
        proposal = os.path.join(inbox, "proposal-" + stem + ".jsonl")
        run_path = os.path.join(inbox, name)
        try:
            parts = json.load(open(run_path, encoding="utf-8")).get("proposal_parts")
        except (json.JSONDecodeError, OSError):
            parts = None
        if parts:
            # Taken in only when every part has synced; a missing one is still on its way.
            if all(
                isinstance(x, dict)
                and os.path.exists(os.path.join(inbox, x.get("file", "")))
                for x in parts
            ):
                reports.append(intake(proposal, run_path, dry_run=dry_run))
        elif os.path.exists(proposal):
            reports.append(intake(proposal, run_path, dry_run=dry_run))
    return reports


# --- CLI ------------------------------------------------------------------------------------


def _say(*a):
    print(*a, file=sys.stderr)


def _print_intake(reports):
    for r in reports:
        _say(
            f"intake {r['proposal']}: {r['status']}"
            + (f" — {r['reason']}" if r.get("reason") else "")
        )
        for row in r["rows"]:
            if row.get("outcome") not in (None, "dropped"):
                _say(f"  {row['group']}: {row['outcome']}")
            for d in row.get("dropped_as_rejected") or []:
                _say(f"    dropped as previously rejected: {d[:100]}")
        for w in r["warnings"]:
            _say(f"  warning: {w}")
        for e in r["errors"]:
            _say(f"  ERROR: {e}")
    if not reports:
        _say("intake: no proposals waiting in the inbox")


def _print_invariant(rep):
    for line in rep["problems"] + rep["repaired"]:
        _say(f"invariant: {line}")
    for line in rep["unrepairable"]:
        _say(f"invariant UNREPAIRABLE: {line}")
    if not (rep["problems"] or rep["unrepairable"]):
        _say(
            "invariant: ok (each version in one file, each doc_id once per approved/pending)"
        )


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sync-prep")
    sp.add_argument("--stage", required=True)
    sp.add_argument("--shas", required=True)
    sp.add_argument("--dry-run", action="store_true")
    ia = sub.add_parser("intake-all")
    ia.add_argument("--dry-run", action="store_true")
    rp = sub.add_parser("repair")
    rp.add_argument("--check", action="store_true")
    sub.add_parser("dump")
    sub.add_parser("sha")
    mv = sub.add_parser("move")
    mv.add_argument("key")
    mv.add_argument("from_state", choices=STATES)
    mv.add_argument("to_state", choices=STATES)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "move":
            print(json.dumps(move(a.key, a.from_state, a.to_state)))
        elif a.cmd == "sha":
            for s in STATES:
                rows, h = load(s)
                print(f"{s}\t{h}\t{len(rows)}")
        elif a.cmd == "dump":
            for s in STATES:
                for d in load(s)[0]:
                    print(json.dumps({**normalize(d), "status": s}, ensure_ascii=False))
        elif a.cmd == "intake-all":
            reports = intake_inbox(dry_run=a.dry_run)
            _print_intake(reports)
            return 1 if any(r["status"] == "error" for r in reports) else 0
        elif a.cmd == "repair":
            rep = check_invariant(repair=not a.check)
            _print_invariant(rep)
            clean = not rep["problems"] and not rep["unrepairable"]
            return 0 if clean or (not a.check and rep["ok"]) else 1
        elif a.cmd == "sync-prep":
            reports = intake_inbox(dry_run=a.dry_run)
            _print_intake(reports)
            if any(r["status"] == "error" for r in reports):
                return 1
            rep = check_invariant(repair=not a.dry_run)
            _print_invariant(rep)
            if rep["unrepairable"]:
                return 1
            if a.dry_run:
                return 0
            loaded = {
                s: load(s) for s in STATES
            }  # one read per file: rows and sha agree
            with open(a.stage, "w", encoding="utf-8") as f:
                for s in STATES:
                    for d in loaded[s][0]:
                        f.write(
                            json.dumps(
                                {**normalize(d), "status": s}, ensure_ascii=False
                            )
                            + "\n"
                        )
            with open(a.shas, "w", encoding="utf-8") as f:
                for s in STATES:
                    f.write(f"{s}\t{loaded[s][1]}\t{len(loaded[s][0])}\n")
            _say("files: " + ", ".join(f"{s} {len(loaded[s][0])}" for s in STATES))
    except Conflict as e:
        _say(f"CONFLICT: {e}")
        return 3
    except StoreError as e:
        _say(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
