#!/usr/bin/env bash
# theme-render.sh <resume.json> <out.pdf> [extra typst args…]
# Render a JSON Resume with the repo's own theme (theme/, docs/adr/0005-own-theme.md) to a
# tagged PDF/UA-1 PDF. The résumé goes in as a build input, so it is never copied into the repo.
# Fonts and style tokens come only from the house-style plugin (github:kattakath/skills, pinned
# in flake.nix; `nix develop` exports HOUSE_STYLE) — system fonts are ignored, so the output is
# the same on every machine.
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

[ -d "${HOUSE_STYLE:-}/typst" ] || {
  echo "error: HOUSE_STYLE is not set to the house-style skill folder — run inside 'nix develop'" >&2
  exit 1
}

theme="$(cd "$(dirname "$0")/../theme" && pwd)"
typst compile \
  --font-path "$HOUSE_STYLE/fonts" --ignore-system-fonts \
  --package-path "$HOUSE_STYLE/typst" \
  --pdf-standard ua-1 \
  --input resume="$(cat "$resume")" \
  "$@" \
  "$theme/resume.typ" "$out"
