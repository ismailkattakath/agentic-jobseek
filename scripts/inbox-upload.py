#!/usr/bin/env python3
"""inbox-upload.py FILE... — upload the routine's proposal and run files to the Drive inbox drop.

    inbox-upload.py window [--since S] [--until U]   # step 1: the run's window and taken stamps

`window` asks the drop for every run file in the inbox, drop/ and processed/ and prints one
JSON line {since, until_at, taken}: since is --since, else the LARGEST until_at among the run
files, else 7 days before now; until_at is --until, else now; a bare date means 00:00:00Z;
taken lists every run file's stamp, comma-separated (ADR-0006 § 4l).

Step 7 of routines/evidence-discover.md (ADR-0006 § 4h). POSTs each file's bytes to the Apps
Script drop ($JOBSEEK_INBOX_DROP_URL; $JOBSEEK_INBOX_DROP_TOKEN goes in the JSON body), in the order given —
pass the run file LAST, because intake takes a run in only once its run file exists. Each answer
carries the sha256 of the bytes the drop wrote; a mismatch is a failed upload. The token is
never printed.

Prints one line per file and exits 1 if any upload failed or the variables are unset.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request


def upload(url, token, path):
    data = open(path, "rb").read()
    want = hashlib.sha256(data).hexdigest()
    name = os.path.basename(path)
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, f"{name}: not UTF-8 text"
    # The token travels in the body, never the URL: proxies and request logs keep URLs.
    try:
        # Apps Script answers 302 to script.googleusercontent.com; urllib follows it with a GET,
        # which is how the web app's response is fetched.
        ans = post(url, {"token": token, "name": name, "content": content})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return False, f"{name}: {type(e).__name__}"
    if not ans.get("ok"):
        return False, f"{name}: drop refused ({ans.get('error', 'no reason')})"
    if ans.get("sha256") != want or ans.get("bytes") != len(data):
        return (
            False,
            f"{name}: drop wrote {ans.get('bytes')} bytes, not the {len(data)} sent",
        )
    return True, f"{name}: {len(data)} bytes, sha256 {want[:12]}… matched"


def post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 — the operator's drop
        return json.loads(r.read().decode("utf-8"))


def iso(value):
    if len(value) == 10:  # a bare date
        value += "T00:00:00Z"
    datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")  # refuses anything else
    return value


def window(url, token, argv, now=None):
    ap = argparse.ArgumentParser(prog="inbox-upload.py window")
    ap.add_argument("--since")
    ap.add_argument("--until")
    a = ap.parse_args(argv)
    ans = post(url, {"token": token, "action": "list"})
    if not ans.get("ok"):
        raise SystemExit(f"window: drop refused ({ans.get('error', 'no reason')})")
    runs = ans["runs"]
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    ends = sorted(r["until_at"] for r in runs if r.get("until_at"))
    since = (
        iso(a.since) if a.since
        else ends[-1] if ends
        else (now - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    )  # fmt: skip
    until_at = iso(a.until) if a.until else now.strftime("%Y-%m-%dT%H:%M:%SZ")
    taken = sorted({r["name"][4:-5] for r in runs})
    return {
        "since": since,
        "until_at": until_at,
        "taken": ",".join(taken),
        "run_files": len(runs),
    }


def main():
    files = sys.argv[1:]
    url = os.environ.get("JOBSEEK_INBOX_DROP_URL", "")
    token = os.environ.get("JOBSEEK_INBOX_DROP_TOKEN", "")
    if not files:
        sys.exit(__doc__)
    if not (url and token):
        print("inbox upload: failed (JOBSEEK_INBOX_DROP_URL or _TOKEN unset)")
        return 1
    if files[0] == "window":
        print(json.dumps(window(url, token, files[1:])))
        return 0
    failed = False
    for f in files:
        ok, line = upload(url, token, f)
        print(("uploaded " if ok else "FAILED ") + line)
        if not ok:
            failed = True
            break  # never upload the run file after a failed proposal
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
