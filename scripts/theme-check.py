#!/usr/bin/env python3
"""theme-check.py <resume.json> <rendered.pdf>

Gate for the own theme (docs/adr/0005-own-theme.md): does an ATS-style text extraction of the
PDF read the résumé IN ORDER and COMPLETE? Two readers, because they disagree in practice:
  geo  `pdftotext`      — rebuilds lines from positions on the page (most ATS parsers)
  raw  `pdftotext -raw` — the PDF's stored text order
Checks, per reader:
  order     section headings and entry titles appear in the theme's source order
  complete  summary, every highlight and every "Skill group: kw, kw…" string appears exactly once
  splits    no word split at a line end ("event-"/"driven") and no "Python/ FastAPI" split
Exit 1 on any failure. Needs poppler (pdftotext). Stdlib only otherwise.
"""

import json
import re
import subprocess
import sys

# The theme's section order (theme/resume.typ). Headings are matched as whole lines.
SECTIONS = ["Summary", "Skills", "Experience", "Projects", "Education"]


def norm(s):
    return re.sub(r"\s+", " ", s)


def extract(pdf, raw):
    args = ["pdftotext"] + (["-raw"] if raw else []) + [pdf, "-"]
    text = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    return text.replace("\f", "\n")  # a page break is a line break


def markers(r):
    """(label, kind, needle) in source order; 'line' = a whole line, 'start' = starts a line."""
    has = {
        "Summary": bool(r.get("basics", {}).get("summary")),
        "Skills": bool(r.get("skills")),
        "Experience": bool(r.get("work")),
        "Projects": bool(r.get("projects")),
        "Education": bool(r.get("education")),
    }
    out = []
    for sec in SECTIONS:
        if not has[sec]:
            continue
        out.append((sec, "line", sec))
        if sec == "Skills":
            out += [
                (f"skill group {i + 1}", "start", s["name"] + ":")
                for i, s in enumerate(r["skills"])
                if s.get("name")
            ]
        if sec == "Experience":
            for i, w in enumerate(r["work"]):
                title = " — ".join(x for x in (w.get("position"), w.get("name")) if x)
                if title:
                    out.append((f"job {i + 1}", "start", title))
        if sec == "Projects":
            out += [
                (f"project {i + 1}", "start", p["name"])
                for i, p in enumerate(r["projects"])
                if p.get("name")
            ]
    return out


def items(r):
    out = []
    if r.get("basics", {}).get("summary"):
        out.append(("basics.summary", r["basics"]["summary"]))
    out += [
        (f"skills[{i}]", s["name"] + ": " + ", ".join(s.get("keywords", [])))
        for i, s in enumerate(r.get("skills", []))
        if s.get("name")
    ]
    for sec in ("work", "projects"):
        for i, e in enumerate(r.get(sec, [])):
            out += [
                (f"{sec}[{i}].highlights[{j}]", h)
                for j, h in enumerate(e.get("highlights", []))
            ]
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    r = json.load(open(sys.argv[1], encoding="utf-8"))
    pdf = sys.argv[2]
    failed = False
    for reader, raw in (("geo", False), ("raw", True)):
        text = extract(pdf, raw)
        flat = norm(text)
        pos = []
        for label, kind, needle in markers(r):
            # Every marker starts a line in the theme (headings, entry titles, skill-group names);
            # anchoring there stops "…SaaS on AWS:" in the summary matching the "AWS:" group.
            words = r"\s+".join(re.escape(w) for w in needle.split())
            tail = r"[ \t]*$" if kind == "line" else ""
            m = re.search(r"(?m)^[ \t]*" + words + tail, text)
            i = len(norm(text[: m.start()])) if m else -1
            pos.append((i, label))
        missing = [lab for i, lab in pos if i < 0]
        got = [lab for _, lab in sorted(p for p in pos if p[0] >= 0)]
        want = [lab for i, lab in pos if i >= 0]
        inversions = sum(
            1
            for a in range(len(got))
            for b in range(a + 1, len(got))
            if want.index(got[a]) > want.index(got[b])
        )
        bad = [lab for lab, s in items(r) if flat.count(norm(s)) != 1]
        splits = len(re.findall(r"[A-Za-z]-\n", text)) + len(
            re.findall(r"[A-Za-z]/ [A-Za-z]", flat)
        )
        ok = not (missing or inversions or bad or splits)
        failed |= not ok
        print(
            f"{reader:4} {'PASS' if ok else 'FAIL'}: order {'ok' if not inversions else f'{inversions} inversions'}"
            f"; missing markers {missing or 'none'}; {len(items(r)) - len(bad)}/{len(items(r))} items once; splits {splits}"
        )
        if inversions:
            print("     read as: " + " > ".join(got))
        if bad:
            print("     not exactly once: " + ", ".join(bad))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
