#!/usr/bin/env python3
"""evidence-schema.py FILE... — validate evidence JSONL against schemas/evidence-doc.schema.json

Every line of every FILE must be one document that matches the schema (docs/adr/0007) and whose
content_hash is sha256(content) — the same recipe evidence-sync.sh enforces. Prints each
violation as FILE:LINE: message and exits 1 if there is any. Also importable (see
evidence-migrate-0007.py): errors(doc) -> [message, ...].

Uses the `jsonschema` package when it is importable (the devShell ships it); otherwise a small
built-in validator covering exactly the keywords the schema uses (type, required, properties,
additionalProperties, enum, pattern, items, minLength).
"""

import hashlib
import json
import os
import re
import sys

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "schemas",
    "evidence-doc.schema.json",
)
with open(SCHEMA_PATH, encoding="utf-8") as _f:
    SCHEMA = json.load(_f)

try:
    import jsonschema

    _VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
    ENGINE = "jsonschema " + __import__("importlib.metadata").metadata.version(
        "jsonschema"
    )
except ImportError:
    _VALIDATOR = None
    ENGINE = "built-in subset"

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
    "number": (int, float),
    "integer": int,
}


def _is(t, v):
    return isinstance(v, _TYPES[t]) and not (t != "boolean" and isinstance(v, bool))


def _subset(s, v, path):
    """The keywords the repo's schemas use (evidence-doc, proposal-line): type (or a list of
    types), enum, const, minLength, pattern, items, minItems, maxItems, required, properties,
    additionalProperties, and allOf of if/then. Identical results everywhere, no dependency."""
    where = "/".join(path) or "document"
    t = s.get("type")
    if t and not any(_is(x, v) for x in (t if isinstance(t, list) else [t])):
        return [f"{where}: expected {t}, got {type(v).__name__}"]
    out = []
    if "const" in s and v != s["const"]:
        out.append(f"{where}: {v!r} is not {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        out.append(f"{where}: {v!r} is not one of {s['enum']}")
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            out.append(f"{where}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]:
            out.append(f"{where}: more than {s['maxItems']} items")
    for sub in s.get("allOf", []):
        if "if" in sub:
            if not _subset(sub["if"], v, path) and "then" in sub:
                out += _subset(sub["then"], v, path)
        else:
            out += _subset(sub, v, path)
    if isinstance(v, str):
        if len(v) < s.get("minLength", 0):
            out.append(f"{where}: {v!r} is shorter than {s['minLength']}")
        if "pattern" in s and not re.search(s["pattern"], v):
            out.append(f"{where}: {v!r} does not match {s['pattern']!r}")
    if isinstance(v, list) and "items" in s:
        for i, x in enumerate(v):
            out += _subset(s["items"], x, path + [str(i)])
    if isinstance(v, dict):
        props = s.get("properties", {})
        out += [
            f"{where}: {k!r} is a required property"
            for k in s.get("required", [])
            if k not in v
        ]
        for k, x in v.items():
            if k in props:
                out += _subset(props[k], x, path + [k])
            elif s.get("additionalProperties", True) is False:
                out.append(f"{where}: additional property {k!r} is not allowed")
    return out


def validate(schema, doc):
    """[message, ...] for one document against any repo schema, with the built-in validator
    (ADR-0006 § 4j: one engine in the cloud and locally, so a verdict never depends on which
    packages happen to be installed)."""
    return _subset(schema, doc, [])


def errors(doc):
    """Every schema violation in one document, plus a content_hash that is not sha256(content)."""
    if _VALIDATOR is not None:
        out = [
            f"{'/'.join(map(str, e.absolute_path)) or 'document'}: {e.message}"
            for e in sorted(
                _VALIDATOR.iter_errors(doc), key=lambda e: list(map(str, e.path))
            )
        ]
    else:
        out = _subset(SCHEMA, doc, [])
    c, h = (
        (doc.get("content"), doc.get("content_hash"))
        if isinstance(doc, dict)
        else (None, None)
    )
    if isinstance(c, str) and isinstance(h, str):
        if hashlib.sha256(c.encode("utf-8")).hexdigest() != h:
            out.append("content_hash: is not sha256(content) — recompute it")
    return out


def check_file(path):
    """[(line_number, message), ...] for every invalid line of a JSONL file."""
    bad = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError as e:
                bad.append((n, f"not JSON ({e})"))
                continue
            bad += [(n, m) for m in errors(doc)]
    return bad


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    failed = 0
    for path in argv:
        bad = check_file(path)
        for n, m in bad:
            print(f"{path}:{n}: {m}")
        failed += bool(bad)
        print(
            f"{os.path.basename(path)}: {'INVALID' if bad else 'ok'} ({len(bad)} violation(s), {ENGINE})",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
