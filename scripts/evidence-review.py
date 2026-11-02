#!/usr/bin/env python3
"""evidence-review.py approve|reject KEY... [--drop TEXT ...] [--edit-content FILE] [--write]

The decision half of docs/adr/0007 § 2.5: the operator's answer to "include this pending
evidence?", applied. KEY is a doc_id or content_hash (or a prefix of 8+ characters) of a
document in evidence/pending.jsonl. Without --write it is a dry run that prints what each
decision would do, including the delta of an enrichment.

  approve  a MOVE, never a copy (evidence-store.py move): pending -> approved.
           new document   appended to approved.jsonl
           enrichment     REPLACES the approved row with the same doc_id; the old content_hash
                          leaves every state file and the sync deletes its Neon row. The
                          proposal's metadata moves into metadata.enrichments[] (the audit
                          trail) and metadata.proposal is removed.
  reject   pending -> rejected.jsonl (either kind); an approved base stays untouched, and
           metadata.proposal stays (it records what was rejected).

  --drop TEXT        approve only, repeatable: cut TEXT out of the sentences the proposal ADDED
                     (a sentence left empty is removed). The "Edit first" answer for a clause.
  --edit-content F   approve only, one KEY only: the whole content becomes the text of file F.
                     The "Edit first" answer for a rewrite.
  Either edit writes a new version of the pending document (new content_hash, backed up) before
  the move, so an interrupted run leaves a valid pending edit, never a half-approval.

--write applies the decisions, then runs scripts/evidence-sync.sh so Neon follows
(--table NAME rehearses against a scratch table; --no-sync skips it).

Exit codes: 0 ok, 1 error, 3 a state file changed while it was being written (re-run).
No qrels remap: judgments key on doc_id, which an enrichment keeps (ADR § 2.2).
"""

import argparse
import copy
import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "evidence_store", os.path.join(HERE, "evidence-store.py")
)
store = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(store)


def say(*a):
    print(*a)


# --- planning -------------------------------------------------------------------------------


def proposal_of(row):
    return (row.get("metadata") or {}).get("proposal") or {}


def added_sentences(row):
    """The sentences a pending ENRICHMENT adds to its approved base, oldest first."""
    prop = proposal_of(row)
    out = list(prop.get("added_sentences") or [])
    for m in prop.get("merged") or []:
        out += [s for s in m.get("added_sentences") or [] if s not in out]
    return out


def _holds(rows, key):
    key = key.strip().lower()
    return any(
        key in (r["doc_id"], r["content_hash"])
        or (len(key) >= 8 and (r["doc_id"].startswith(key) or r["content_hash"].startswith(key)))
        for r in rows
    )


def resolve_all(keys):
    """Resolve every KEY in pending.jsonl before anything is touched. Returns [row, ...]."""
    pending, _ = store.load("pending")
    rows, seen, errors = [], set(), []
    for key in keys:
        try:
            row = store._resolve(pending, key, "pending")
        except store.StoreError as e:
            msg = str(e)
            if " matches 0 documents" in msg:  # not pending: say where it is, if anywhere
                where = [s for s in ("approved", "rejected") if _holds(store.load(s)[0], key)]
                if where:
                    msg += f" — it is already in {', '.join(where)}.jsonl"
            errors.append(msg)
            continue
        if row["content_hash"] not in seen:
            seen.add(row["content_hash"])
            rows.append(row)
    if errors:
        raise store.StoreError("\n".join(errors))
    return rows


def clean(s):
    s = re.sub(r"\s{2,}", " ", s)
    return re.sub(r"\s+([,.;:])", r"\1", s).strip()


def drop_text(row, texts):
    """Return (new_row, unmatched_texts). Cuts each TEXT out of the sentences this proposal ADDED;
    approved base text is never touched. A pending NEW document has no base, so any text goes."""
    row = copy.deepcopy(row)
    prop = row.setdefault("metadata", {}).setdefault("proposal", {})
    content, unmatched = row["content"], []
    lists = [prop] + [m for m in prop.get("merged") or []]
    for t in texts:
        hits = [s for s in added_sentences(row) if t in s]
        if hits:
            for s in hits:
                kept = clean(s.replace(t, ""))
                if kept.strip(" .,;:"):
                    content = content.replace(s, kept, 1)
                else:
                    content = content.replace(" " + s, "", 1) if " " + s in content else content.replace(s, "", 1)
                    kept = None
                for holder in lists:
                    sents = holder.get("added_sentences")
                    if sents and s in sents:
                        holder["added_sentences"] = [kept if x == s else x for x in sents if x != s or kept]
        elif prop.get("kind") == "new" and t in content:
            content = re.sub(r"[ \t]{2,}", " ", content.replace(t, "")).strip()
        else:
            unmatched.append(t)
    row["content"] = content
    prop.setdefault("edited", {}).setdefault("dropped", []).extend(t for t in texts if t not in unmatched)
    return row, unmatched


def replace_content(row, text):
    row = copy.deepcopy(row)
    row["content"] = text
    prop = row.setdefault("metadata", {}).setdefault("proposal", {})
    prop.pop("added_sentences", None)  # no longer describes the content
    for m in prop.get("merged") or []:
        m.pop("added_sentences", None)
    prop.setdefault("edited", {})["content_replaced"] = True
    return row


def finish_edit(old, new):
    """Re-hash an edited row and refuse a version that already exists elsewhere."""
    if not new["content"].strip():
        raise store.StoreError("the edit leaves no content")
    new["content_hash"] = store.sha(new["content"])
    if new["content_hash"] == old["content_hash"]:
        raise store.StoreError(f"{old['title']!r}: the edit changes nothing")
    for s in store.STATES:
        if any(r["content_hash"] == new["content_hash"] for r in store.load(s)[0]):
            raise store.StoreError(f"{old['title']!r}: the edited text is already a version in {s}.jsonl")
    return new


def plan(action, row):
    """What store.move will do, for the dry run and the confirmation line."""
    approved, _ = store.load("approved")
    base = next((r for r in approved if r["doc_id"] == row["doc_id"] and r["content_hash"] != row["content_hash"]), None)
    prop = proposal_of(row)
    p = {"action": action, "row": row, "base": base, "warnings": []}
    if action == "reject":
        p["kind"] = "to-rejected"
    elif base:
        p["kind"] = "approve-enrich"
        if prop.get("base_hash") and prop["base_hash"] != base["content_hash"]:
            p["warnings"].append(
                f"built on approved version {prop['base_hash'][:12]}, but {base['content_hash'][:12]} is approved now — "
                "approving REPLACES the current text with this version"
            )
    else:
        p["kind"] = "approve-new"
    return p


def show(p, indent="  "):
    row, base = p["row"], p["base"]
    kind = {
        "approve-new": "APPROVE new document",
        "approve-enrich": "APPROVE enrichment",
        "to-rejected": "REJECT " + ("enrichment (the approved version stays)" if p["base"] else "document"),
    }[p["kind"]]
    say(f"{kind}: {row['title']}")
    h = row["content_hash"][:12]
    if base:
        h = f"{base['content_hash'][:12]} -> {h}"
    say(f"{indent}doc_id {row['doc_id']}  hash {h}")
    say(f"{indent}{row.get('source_class')} · {row.get('period')} · {row.get('company') or row.get('repo') or '-'}")
    adds = added_sentences(row) if base else []
    if base and adds:
        say(f"{indent}+{len(adds)} sentence(s):")
        for s in adds:
            say(f"{indent}  + {s}")
    elif base:
        say(f"{indent}content: {len(base['content'])} -> {len(row['content'])} characters")
    elif p["action"] == "approve":
        say(f"{indent}{row['content'][:400]}{'…' if len(row['content']) > 400 else ''}")
    edited = proposal_of(row).get("edited")
    if edited:
        say(f"{indent}edited: " + ("content replaced" if edited.get("content_replaced") else f"dropped {edited['dropped']}"))
    for w in p["warnings"]:
        say(f"{indent}warning: {w}")


# --- main -----------------------------------------------------------------------------------


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("approve", "reject"))
    ap.add_argument("keys", nargs="+", metavar="KEY", help="doc_id or content_hash (or 8+ char prefix) in pending.jsonl")
    ap.add_argument("--drop", action="append", default=[], metavar="TEXT")
    ap.add_argument("--edit-content", metavar="FILE")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--table", help="rehearse the sync against this scratch Neon table")
    ap.add_argument("--no-sync", action="store_true", help="move only; run evidence-sync.sh yourself")
    a = ap.parse_args(argv)
    try:
        if (a.drop or a.edit_content) and a.action != "approve":
            raise store.StoreError("--drop / --edit-content only apply to approve")
        if a.drop and a.edit_content:
            raise store.StoreError("--drop and --edit-content are two different edits — use one")
        rows = resolve_all(a.keys)
        if a.edit_content and len(rows) != 1:
            raise store.StoreError("--edit-content replaces a whole document's text — name exactly one KEY")

        edits = {}  # old content_hash -> edited row
        if a.drop:
            used = set()
            for row in rows:
                new, unmatched = drop_text(row, a.drop)
                used |= set(a.drop) - set(unmatched)
                if new["content"] != row["content"]:
                    edits[row["content_hash"]] = finish_edit(row, new)
            missing = [t for t in a.drop if t not in used]
            if missing:
                raise store.StoreError(
                    f"--drop {missing!r}: no sentence that the proposal added contains it "
                    "(approved text is never cut; use --edit-content to rewrite)"
                )
        if a.edit_content:
            text = open(a.edit_content, encoding="utf-8").read().rstrip("\n")
            edits[rows[0]["content_hash"]] = finish_edit(rows[0], replace_content(rows[0], text))

        plans = [plan(a.action, edits.get(r["content_hash"], r)) for r in rows]
        for p in plans:
            show(p)
        if not a.write:
            say("dry run — re-run with --write to apply")
            return 0

        done, failure = [], None
        try:
            for r in rows:
                if r["content_hash"] in edits:
                    pending, sha = store.load("pending")
                    edited = edits[r["content_hash"]]
                    store.write(
                        "pending",
                        [edited if x["content_hash"] == r["content_hash"] else x for x in pending],
                        sha,
                        reason="review-edit",
                    )
                    r = edited
                result = store.move(r["doc_id"], "pending", "approved" if a.action == "approve" else "rejected")
                done.append(result)
                say(
                    f"moved {result['kind']}: {result['title']} ({result['doc_id']})"
                    + (f"; replaced {result['replaced_hash'][:12]}" if result["replaced_hash"] else "")
                )
        except store.StoreError as e:
            failure = e  # what moved stays moved; sync it, then report
        if done and not a.no_sync:
            say("syncing Neon (scripts/evidence-sync.sh)…")
            sys.stdout.flush()
            cmd = [os.path.join(HERE, "evidence-sync.sh")] + (["--table", a.table] if a.table else [])
            rc = subprocess.run(cmd).returncode
            if rc:
                say(f"error: the decision is saved in the state files, but evidence-sync.sh exited {rc} — re-run it")
                return rc
        if failure:
            raise failure
        remaining = [r for r in rows if r["doc_id"] not in {d["doc_id"] for d in done}]
        if remaining:
            raise store.StoreError(f"{len(remaining)} document(s) not moved")
        say(f"{len(done)} document(s) {'approved' if a.action == 'approve' else 'rejected'}")
        return 0
    except store.Conflict as e:
        print(f"CONFLICT: {e}", file=sys.stderr)
        return 3
    except store.StoreError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
