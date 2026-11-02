#!/usr/bin/env bash
# job-evidence.sh <application-folder> [--scope enterprise|personal-project|oss-contribution] [--k N]
# The evidence gate as a checklist: one scripts/evidence.sh query per requirement in the
# folder's job.json (every responsibility, qualification and skill), written to
# <folder>/evidence.md for the tailor and the reviewers to cite. A requirement whose top hits
# are all weak is still listed — deciding backed vs gap is the tailor's call, made against the
# rows, never against memory (.claude/rules/evidence-gate.md).
set -euo pipefail

usage() {
  echo "usage: scripts/job-evidence.sh <application-folder> [--scope S] [--k N]" >&2
  exit 2
}
[ "$#" -ge 1 ] || usage
dir="${1%/}"
shift
scope=()
k=3
while [ "$#" -gt 0 ]; do
  case "$1" in
  --scope)
    scope=(--scope "${2:-}")
    shift 2
    ;;
  --k)
    k="${2:-3}"
    shift 2
    ;;
  *) usage ;;
  esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
job="$dir/job.json"
[ -f "$job" ] || {
  echo "error: no job.json in $dir — extract it from posting.md first" >&2
  exit 1
}

out="$dir/evidence.md"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
python3 "$here/job-validate.py" "$job" --items >"$tmp"

{
  echo "# Evidence per requirement"
  echo
  echo "Generated $(date +%F) by scripts/job-evidence.sh from job.json (k=$k${scope[1]:+, scope=${scope[1]}})."
  echo "Each block lists the store's top rows; a gap is decided by reading them, not by their absence."
  echo
  while IFS= read -r req || [ -n "$req" ]; do
    [ -n "$req" ] || continue
    echo "## $req"
    echo
    "$here/evidence.sh" "$req" --k "$k" "${scope[@]}" | sed 's/^/- /'
    echo
  done <"$tmp"
} >"$out"
echo "wrote $out ($(grep -c '^## ' "$out") requirements)" >&2
