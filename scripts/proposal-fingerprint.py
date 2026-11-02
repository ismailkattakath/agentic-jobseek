#!/usr/bin/env python3
"""proposal-fingerprint.py <proposal.jsonl> — print the proposal's content fingerprint.

The evidence-discover routine records this in its run file; scripts/evidence-store.py (intake, run by
scripts/evidence-sync.sh) recomputes it and refuses a proposal that differs (docs/adr/0006 § 4d,
docs/adr/0007 § 3). It hashes the JSON CONTENT — each line parsed
and re-serialised with sorted keys — so whitespace or a trailing newline added by the Drive
connector does not matter, while any changed, dropped or added value does.
"""

import hashlib
import json
import sys


def fingerprint(path):
    lines = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    canon = "\n".join(
        json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for o in lines
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(fingerprint(sys.argv[1]))
