#!/usr/bin/env python3
"""evidence-metrics.py [--eval] [--dry-run] — push evidence-pipeline gauges to Grafana Cloud.

docs/adr/0007 § 5 (cross-run trends). Sends ONE OTLP/HTTP JSON metrics request (gauges):

  evidence_docs{status=approved|pending|rejected}   documents per state file
  evidence_pending_oldest_age_days                  oldest pending document, in days (0 if none)
  evidence_last_discovery_until_seconds             newest run-*.json `until_at`, unix seconds
  jobseek_applications{stage}                       role folders per stage, mapped to a CLOSED enum
                                                    (draft|ready|applied|interview|offer|closed|other)
  jobseek_applied{window=7d|28d}                    applications with an `applied:` date in the window
  jobseek_days_since_last_application               newest `applied:` date, in days (absent if none)
  jobseek_next_action_overdue                       `next_action_due:` already past
  jobseek_status_drift{kind}                        applied_without_date | unknown_stage
  (from $JOBSEEK_DRIVE_DIR/applications/*/*/status.md frontmatter; no company or role leaves)
  with --eval, per system in {hybrid, vector, bm25} (from `retrieval-eval.py score --json`):
  evidence_retrieval_ndcg_at_10 / _recall_at_10 / _mrr {system=...}

Resource attributes: service.name=agentic-jobseek, deployment.environment=operator-mac.
Idempotent (gauges: a re-push only adds a sample), quiet on success, exit 1 with the HTTP code
on a failed push, exit 3 when push is not configured here (no credentials: callers skip silently).

Configuration (all in the gitignored .envrc.local — none of it is a secret except the token):
  JOBSEEK_OTLP_ENDPOINT   e.g. https://otlp-gateway-<region>.grafana.net/otlp
  JOBSEEK_OTLP_INSTANCE   the stack's numeric instance id (basic-auth user)
  JOBSEEK_OTLP_SECRET     Keychain item holding the token; read via `secret exec`, never argv
  JOBSEEK_OTLP_TOKEN      the token itself, if the caller already supplied it (tests, CI)

  --eval      also run the retrieval eval (reads Neon, embeds via Ollama) and push its quality point
  --dry-run   print the payload and exit; sends nothing and needs no credentials

Metric names as stored in Prometheus can gain a unit suffix on ingest; units here are chosen so
the names above survive unchanged (curly-brace units are dropped, `d`/`s` match the suffix).
"""

import argparse
import base64
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESOURCE = {"service.name": "agentic-jobseek", "deployment.environment": "operator-mac"}
NO_CREDENTIALS = 3


def load_store():
    spec = importlib.util.spec_from_file_location(
        "evidence_store", os.path.join(HERE, "evidence-store.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_stamp(s):
    """Epoch seconds of a proposal stamp (20261102T1926Z, 2026-11-02) or an ISO timestamp, else None."""
    for fmt in ("%Y%m%dT%H%MZ", "%Y-%m-%d"):
        try:
            return (
                datetime.datetime.strptime(s, fmt)
                .replace(tzinfo=datetime.timezone.utc)
                .timestamp()
            )
        except ValueError:
            pass
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)).timestamp()


def store_gauges(store, now):
    """[(name, unit, attrs, value)] for the three state files and the discovery watermark."""
    out = []
    pending = []
    for state in store.STATES:
        rows, _ = store.load(state)
        out.append(("evidence_docs", "{documents}", {"status": state}, len(rows)))
        if state == "pending":
            pending = rows
    # Same rule as scripts/evidence-digest.sh: metadata.proposal.stamp, else created_at.
    stamps = [
        parse_stamp(
            str(
                ((r.get("metadata") or {}).get("proposal") or {}).get("stamp")
                or r.get("created_at")
                or ""
            )
        )
        for r in pending
    ]
    stamps = [t for t in stamps if t is not None]
    age = max(0, int((now - min(stamps)) // 86400)) if stamps else 0
    out.append(("evidence_pending_oldest_age_days", "d", {}, age))

    # The newest `until_at` of any run file in the inbox folders (depth <= 2, as the digest does).
    newest = None
    inbox = os.path.join(store.root(), "inbox")
    for dirpath, _dirs, files in os.walk(inbox):
        if dirpath[len(inbox) :].count(os.sep) > 1:
            continue
        for name in files:
            if name.startswith("run-") and name.endswith(".json"):
                try:
                    with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                        t = parse_stamp(str(json.load(f).get("until_at") or ""))
                except (OSError, ValueError, AttributeError):
                    continue
                if t is not None and (newest is None or t > newest):
                    newest = t
    if newest is not None:
        out.append(("evidence_last_discovery_until_seconds", "s", {}, int(newest)))
    return out


STAGES = {
    "draft": "draft",
    "ready": "ready",
    "ready to apply": "ready",
    "applied": "applied",
    "interview": "interview",
    "interviewing": "interview",
    "screen": "interview",
    "offer": "offer",
    "rejected": "closed",
    "withdrawn": "closed",
    "closed": "closed",
    "declined": "closed",
}


def frontmatter(path):
    lines = path.read_text(errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def funnel_gauges(now):
    """Counts only. The stage map is applied at READ time: the Drive files are not edited."""
    from pathlib import Path

    root = os.environ.get("JOBSEEK_DRIVE_DIR")
    if not root:
        return []
    files = sorted(Path(root).glob("applications/*/*/status.md"))
    today = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date()
    counts = {s: 0 for s in sorted(set(STAGES.values())) + ["other"]}
    applied_dates, overdue, no_date = [], 0, 0
    for f in files:
        fm = frontmatter(f)
        stage = STAGES.get(fm.get("stage", "").lower(), "other")
        counts[stage] += 1
        applied = fm.get("applied")
        if applied and applied != "null":
            try:
                applied_dates.append(datetime.date.fromisoformat(applied[:10]))
            except ValueError:
                no_date += stage in ("applied", "interview", "offer")
        elif stage in ("applied", "interview", "offer"):
            no_date += 1
        due = fm.get("next_action_due")
        if due and due != "null":
            try:
                overdue += datetime.date.fromisoformat(due[:10]) < today
            except ValueError:
                pass
    g = [
        ("jobseek_applications", "{count}", {"stage": s}, n) for s, n in counts.items()
    ]
    for window, days in (("7d", 7), ("28d", 28)):
        g.append(
            (
                "jobseek_applied",
                "{count}",
                {"window": window},
                sum((today - d).days < days for d in applied_dates),
            )
        )
    if applied_dates:
        g.append(
            (
                "jobseek_days_since_last_application",
                "d",
                {},
                (today - max(applied_dates)).days,
            )
        )
    g.append(("jobseek_next_action_overdue", "{count}", {}, overdue))
    g.append(
        ("jobseek_status_drift", "{count}", {"kind": "applied_without_date"}, no_date)
    )
    g.append(
        ("jobseek_status_drift", "{count}", {"kind": "unknown_stage"}, counts["other"])
    )
    return g


def eval_gauges():
    """Quality gauges from `retrieval-eval.py score --json` (it re-execs itself under `secret exec`)."""
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "retrieval-eval.py"), "score", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [x for x in p.stdout.splitlines() if x.startswith("{")]
    if p.returncode != 0 or not lines:
        sys.exit(
            f"error: retrieval-eval.py score failed (exit {p.returncode}): {p.stderr.strip()[-300:]}"
        )
    res = json.loads(lines[-1])["systems"]
    names = {
        "ndcg10": "evidence_retrieval_ndcg_at_10",
        "recall10": "evidence_retrieval_recall_at_10",
        "mrr": "evidence_retrieval_mrr",
    }
    return [
        (names[k], "{score}", {"system": s}, v[k])
        for s, v in res.items()
        for k in names
    ]


def attr(k, v):
    return {"key": k, "value": {"stringValue": v}}


def payload(gauges, now):
    ts = str(int(now * 1e9))
    by_name = {}
    for name, unit, attrs, value in gauges:
        m = by_name.setdefault(
            name, {"name": name, "unit": unit, "gauge": {"dataPoints": []}}
        )
        point = {
            "attributes": [attr(k, v) for k, v in attrs.items()],
            "timeUnixNano": ts,
        }
        point["asInt" if isinstance(value, int) else "asDouble"] = (
            str(value) if isinstance(value, int) else value
        )
        m["gauge"]["dataPoints"].append(point)
    return {
        "resourceMetrics": [
            {
                "resource": {"attributes": [attr(k, v) for k, v in RESOURCE.items()]},
                "scopeMetrics": [
                    {
                        "scope": {"name": "agentic-jobseek.evidence-metrics"},
                        "metrics": list(by_name.values()),
                    }
                ],
            }
        ]
    }


def token():
    """The token from the environment, else re-exec under `secret exec` so it never touches argv."""
    tok = os.environ.get("JOBSEEK_OTLP_TOKEN")
    if tok:
        return tok
    item = os.environ.get("JOBSEEK_OTLP_SECRET")
    if not item:
        sys.exit(NO_CREDENTIALS)
    try:
        found = (
            subprocess.run(
                ["secret", "fp", item], capture_output=True, check=False
            ).returncode
            == 0
        )
    except FileNotFoundError:  # no `secret` CLI on this machine
        found = False
    if not found:
        sys.exit(NO_CREDENTIALS)  # Keychain item missing
    os.execvp(
        "secret",
        [
            "secret",
            "exec",
            f"JOBSEEK_OTLP_TOKEN={item}",
            "--",
            sys.executable,
            *sys.argv,
        ],
    )


def push(body):
    endpoint = os.environ.get("JOBSEEK_OTLP_ENDPOINT")
    instance = os.environ.get("JOBSEEK_OTLP_INSTANCE")
    if not endpoint or not instance:
        sys.exit(NO_CREDENTIALS)
    auth = base64.b64encode(f"{instance}:{token()}".encode()).decode()
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/metrics",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 — https endpoint from the operator's env
            r.read()
    except urllib.error.HTTPError as e:
        sys.exit(
            f"error: OTLP push failed: HTTP {e.code} {e.read().decode(errors='replace')[:200]}"
        )
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"error: OTLP push failed: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--eval",
        action="store_true",
        help="also run the retrieval eval and push its quality point",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print the payload; send nothing"
    )
    args = ap.parse_args()
    if not args.dry_run and not os.environ.get("JOBSEEK_OTLP_TOKEN"):
        # Resolve credentials BEFORE the slow work, so the secret-exec re-exec costs nothing twice.
        if not (
            os.environ.get("JOBSEEK_OTLP_ENDPOINT")
            and os.environ.get("JOBSEEK_OTLP_INSTANCE")
        ):
            sys.exit(NO_CREDENTIALS)
        token()
    now = time.time()
    store = load_store()
    try:
        gauges = store_gauges(store, now)
    except store.StoreError as e:
        sys.exit(f"error: {e}")
    gauges += funnel_gauges(now)
    if args.eval:
        gauges += eval_gauges()
    body = payload(gauges, now)
    if args.dry_run:
        print(json.dumps(body, indent=2))
        return
    push(body)


if __name__ == "__main__":
    main()
