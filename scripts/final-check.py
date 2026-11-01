#!/usr/bin/env python3
"""final-check.py <application-folder> [--max-pages N]

Deterministic pre-submission gate for a rendered application. Reads the
folder's resume.json and its submitted PDF (<FullName>-<Role>.pdf) and checks
what an applicant-tracking system (ATS) does first: flatten the PDF to text,
find the name, contacts, section headings and one date per role.

  FAIL (exit 1)  leftover placeholders, zero-width characters, name not the
                 first line, email/phone missing, a work entry with no date,
                 no recognisable Experience/Skills/Education heading.
  WARN           more pages than --max-pages (default 2), missing ATS keywords
                 is NOT checked here (the final-pass review does that).

Needs pdfinfo/pdftotext (poppler). Stdlib only otherwise.
Origin: the 2026-10-28 résumé panel found a 3-page overflow and Google-export
zero-width spaces that no review agent caught; both are cheap to measure.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

HEADINGS = {
    'experience': ('experience', 'professional experience', 'work experience', 'employment'),
    'skills': ('skills', 'technical skills', 'core skills'),
    'education': ('education',),
}
MONTH = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{4}'
# "[N tenants?]" style; the closing bracket is optional because extraction can
# split or reorder it (seen when a placeholder wraps across lines).
PLACEHOLDER = re.compile(r'\[[^\[\]\n]{0,60}\?\]?|\bTODO\b|\bTBD\b|\bXX+\b|lorem ipsum', re.I)
ZERO_WIDTH = re.compile('[​‌‍⁠﻿]')


def run(cmd):
    return subprocess.check_output(cmd, text=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('folder')
    ap.add_argument('--max-pages', type=int, default=2)
    args = ap.parse_args()

    folder = args.folder.rstrip('/')
    resume = json.load(open(os.path.join(folder, 'resume.json')))
    basics = resume.get('basics', {})
    name = basics.get('name', '')
    pdfs = sorted(glob.glob(os.path.join(folder, '*.pdf')), key=os.path.getmtime)
    if not pdfs:
        sys.exit(f'error: no PDF in {folder} — run scripts/render.sh first')
    pdf = pdfs[-1]

    pages = int(re.search(r'Pages:\s+(\d+)', run(['pdfinfo', pdf])).group(1))
    text = run(['pdftotext', pdf, '-'])
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    lower = {l.lower() for l in lines}
    flat = re.sub(r'\s+', ' ', text)
    digits = re.sub(r'\D', '', flat)

    results = []  # (level, check, detail)

    def add(ok, check, detail, level='FAIL'):
        results.append(('PASS' if ok else level, check, detail))

    add(pages <= args.max_pages, 'pages', f'{pages} (limit {args.max_pages})', level='WARN')
    add(bool(lines) and name and lines[0] == name, 'name is the first line',
        repr(lines[0] if lines else ''))
    email = basics.get('email', '')
    add(not email or email in flat, 'email parses', email or '(none in resume.json)')
    phone = re.sub(r'\D', '', basics.get('phone', ''))
    add(not phone or phone in digits, 'phone parses', basics.get('phone', '') or '(none)')
    for key, names in HEADINGS.items():
        present = key != 'skills' or resume.get('skills')
        add(not present or any(n in lower for n in names), f'{key} heading',
            'found' if any(n in lower for n in names) else 'missing')
    work = resume.get('work', [])
    dates = len(re.findall(MONTH, text))
    add(dates >= len(work), 'a date per work entry', f'{dates} dates for {len(work)} roles')
    ph = PLACEHOLDER.findall(text)
    add(not ph, 'no placeholders', ', '.join(ph) or 'none')
    zw = len(ZERO_WIDTH.findall(text))
    add(zw == 0, 'no zero-width characters', str(zw))

    print(f'final-check: {pdf}')
    for level, check, detail in results:
        print(f'  {level:4}  {check:28} {detail}')
    fails = [r for r in results if r[0] == 'FAIL']
    warns = [r for r in results if r[0] == 'WARN']
    print(f'  -> {len(fails)} fail, {len(warns)} warn')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
