#!/usr/bin/env python3
"""inbox-upload.py FILE... — upload the routine's proposal and run files to the Drive inbox drop.

Step 7 of routines/evidence-discover.md (ADR-0006 § 4h). POSTs each file's bytes to the Apps
Script drop ($JOBSEEK_INBOX_DROP_URL; $JOBSEEK_INBOX_DROP_TOKEN goes in the JSON body), in the order given —
pass the run file LAST, because intake takes a run in only once its run file exists. Each answer
carries the sha256 of the bytes the drop wrote; a mismatch is a failed upload. The token is
never printed.

Prints one line per file and exits 1 if any upload failed or the variables are unset.
"""

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
    body = json.dumps({"token": token, "name": name, "content": content}).encode(
        "utf-8"
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Apps Script answers 302 to script.googleusercontent.com; urllib follows it with a GET,
        # which is how the web app's response is fetched.
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — the operator's drop
            ans = json.loads(r.read().decode("utf-8"))
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


def main():
    files = sys.argv[1:]
    url = os.environ.get("JOBSEEK_INBOX_DROP_URL", "")
    token = os.environ.get("JOBSEEK_INBOX_DROP_TOKEN", "")
    if not files:
        sys.exit(__doc__)
    if not (url and token):
        print("inbox upload: failed (JOBSEEK_INBOX_DROP_URL or _TOKEN unset)")
        return 1
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
