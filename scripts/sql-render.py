#!/usr/bin/env python3
"""sql-render.py <file.sql> name=value ... — substitute psql variables, print plain SQL.

The cloud routine reaches Neon only through the connector's run_sql, which takes one plain
statement, not psql. This applies psql's own variable rules to a shared SQL file so the routine
runs exactly what scripts/evidence.sh runs (docs/adr/0006):
  :'name'  → the value as a quoted SQL literal ('' doubles a quote)
  :name    → the value raw (identifiers, numbers, a select list)
`cols` is substituted first, so a select list may itself use :'qv' and friends. Comment lines
are dropped; a variable the file uses but the call does not set is an error, except those in
DEFAULTS (`statuses`: approved documents only, docs/adr/0007 § 2.4).
"""

import re
import sys


DEFAULTS = {"statuses": "{approved}"}


def render(text, values):
    values = {**DEFAULTS, **values}
    text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    if "cols" in values:
        text = text.replace(":cols", values["cols"])

    def sub(m):
        quoted, name = m.group(1) is not None, m.group(1) or m.group(2)
        if name not in values:
            sys.exit(f"error: the SQL uses :{name} but no {name}=… was given")
        v = values[name]
        return "'" + v.replace("'", "''") + "'" if quoted else v

    # :'name' or :name — but not a :: cast
    return re.sub(r"(?<!:):(?:'([A-Za-z_]\w*)'|([A-Za-z_]\w*))", sub, text).strip()


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    values = {}
    for arg in sys.argv[2:]:
        name, sep, value = arg.partition("=")
        if not sep:
            sys.exit(f"error: expected name=value, got {arg!r}")
        values[name] = value
    with open(sys.argv[1], encoding="utf-8") as f:
        print(render(f.read(), values))


if __name__ == "__main__":
    main()
