#!/usr/bin/env bash
# theme-render.sh <resume.json> <out.pdf> [extra typst args…]
# Render a JSON Resume with the repo's own theme (theme/, docs/adr/0005-own-theme.md) to a
# tagged PDF/UA-1 PDF. The résumé goes in as a build input, so it is never copied into the repo.
# Fonts come only from theme/fonts (vendored Google Sans, OFL) — system fonts are ignored, so the
# output is the same on every machine.
set -euo pipefail

[ "$#" -ge 2 ] || {
  echo "usage: scripts/theme-render.sh <resume.json> <out.pdf> [extra typst args]" >&2
  exit 2
}
command -v typst >/dev/null 2>&1 || {
  echo "error: 'typst' not found on PATH — run inside 'nix develop'" >&2
  exit 1
}

resume="$1"
out="$2"
shift 2
[ -f "$resume" ] || {
  echo "error: no such file: $resume" >&2
  exit 1
}

theme="$(cd "$(dirname "$0")/../theme" && pwd)"
typst compile \
  --font-path "$theme/fonts" --ignore-system-fonts \
  --pdf-standard ua-1 \
  --input resume="$(cat "$resume")" \
  "$@" \
  "$theme/resume.typ" "$out"
