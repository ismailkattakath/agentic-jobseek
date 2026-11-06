#!/usr/bin/env python3
"""run-stats.py RUN_FILE [--dry-run]

Step 11 of routines/evidence-discover.md: push one OTLP metrics payload describing a discovery run
to $JOBSEEK_OTLP_ENDPOINT/v1/metrics, built from the run file step 9 wrote (no number retyped).
The run's start is its `until_at` (step 1 sets until_at to the run's start time), so no side file
is needed. Sends NO Authorization header: in the cloud environment a network secret injects it.

Prints exactly one line, `run stats: HTTP <code>` or `run stats: skipped|failed (<reason>)`, and
always exits 0 — this step observes the run, it is never part of it.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime


def gauge(name, unit, points, now):
    return {
        "name": name,
        "unit": unit,
        "gauge": {
            "dataPoints": [
                {
                    "attributes": [
                        {"key": k, "value": {"stringValue": v}}
                        for k, v in attrs.items()
                    ],
                    "timeUnixNano": now,
                    "asInt": str(n),
                }
                for attrs, n in points
            ]
        },
    }


def payload(run):
    now = str(time.time_ns())
    started = datetime.fromisoformat(run["until_at"].replace("Z", "+00:00")).timestamp()
    p = run.get("proposals", {})
    metrics = [
        gauge("evidence_discover_prs", "{prs}", [({}, run.get("github_prs", 0))], now),
        gauge(
            "evidence_discover_commits",
            "{commits}",
            [({}, run.get("github_commits", 0))],
            now,
        ),
        gauge(
            "evidence_discover_issues",
            "{issues}",
            [({}, run.get("github_issues", 0))],
            now,
        ),
        gauge(
            "evidence_discover_groups", "{groups}", [({}, run.get("groups", 0))], now
        ),
        gauge(
            "evidence_discover_proposals",
            "{proposals}",
            [({"action": k}, p.get(k, 0)) for k in ("enrich", "new", "skip")],
            now,
        ),
        gauge(
            "evidence_discover_errors",
            "{errors}",
            [({}, len(run.get("errors", [])))],
            now,
        ),
        gauge(
            "evidence_discover_run_duration_seconds",
            "s",
            [({}, max(0, int(time.time() - started)))],
            now,
        ),
    ]
    res = {
        "attributes": [
            {"key": "service.name", "value": {"stringValue": "evidence-discover"}}
        ]
    }
    return {
        "resourceMetrics": [
            {
                "resource": res,
                "scopeMetrics": [
                    {"scope": {"name": "evidence-discover"}, "metrics": metrics}
                ],
            }
        ]
    }


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if len(args) != 1:
        print("run stats: failed (usage: run-stats.py RUN_FILE [--dry-run])")
        return 0
    try:
        body = json.dumps(payload(json.load(open(args[0], encoding="utf-8")))).encode()
    except (OSError, ValueError, KeyError) as e:
        print(f"run stats: failed (run file: {type(e).__name__})")
        return 0
    if dry:
        print(body.decode())
        return 0
    endpoint = os.environ.get("JOBSEEK_OTLP_ENDPOINT", "")
    if not (endpoint and os.environ.get("JOBSEEK_OTLP_INSTANCE")):
        print("run stats: skipped (OTLP variables unset)")
        return 0
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/metrics",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"run stats: failed ({type(e).__name__})")
        return 0
    hint = (
        " — network secret missing or host not allowlisted"
        if code in (401, 403)
        else ""
    )
    print(f"run stats: HTTP {code}{hint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
