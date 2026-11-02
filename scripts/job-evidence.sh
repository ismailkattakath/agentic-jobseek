#!/usr/bin/env bash
# job-evidence.sh <application-folder> [--scope enterprise|personal-project|oss-contribution] [--k N] [--approved-only]
# The evidence gate as a checklist: one scripts/evidence.sh query per requirement in the
# folder's job.json (every responsibility, qualification and skill), written to
# <folder>/evidence.md for the tailor and the reviewers to cite. A requirement whose top hits
# are all weak is still listed — deciding backed vs gap is the tailor's call, made against the
# rows, never against memory (.claude/rules/evidence-gate.md).
#
# Pending rows (ADR-0007 § 2.4) are retrieved too (evidence.sh --with-pending) and MARKED
# "[PENDING doc_id=… hash=…]" so the tailor can ask the operator about them; a pending row is
# never evidence until approved. Contract with evidence.sh --with-pending: every output row
# is tab-separated with status, doc_id, content_hash as the FIRST three fields, then the
# columns evidence.sh printed before. Pass --approved-only to retrieve approved rows only.
set -euo pipefail

usage() {
  echo "usage: scripts/job-evidence.sh <application-folder> [--scope S] [--k N] [--approved-only]" >&2
  exit 2
}
[ "$#" -ge 1 ] || usage
dir="${1%/}"
shift
scope=()
k=3
pending=(--with-pending)
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
  --approved-only)
    pending=()
    shift
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
  echo "Rows marked [PENDING …] are unreviewed: they are NOT evidence until the operator approves them (tailor-application § 3a)."
  echo "Each block lists the store's top rows; a gap is decided by reading them, not by their absence."
  echo
  while IFS= read -r req || [ -n "$req" ]; do
    [ -n "$req" ] || continue
    echo "## $req"
    echo
    "$here/evidence.sh" "$req" --k "$k" "${pending[@]}" "${scope[@]}" |
      awk -F'\t' -v OFS='\t' '
        NF >= 3 && $1 == "pending" {
          s = "- [PENDING doc_id=" $2 " hash=" substr($3, 1, 12) "] "
          $1 = $2 = $3 = ""; sub(/^\t+/, ""); print s $0; next }
        NF >= 3 && ($1 == "approved" || $1 == "rejected") {
          $1 = $2 = $3 = ""; sub(/^\t+/, ""); print "- " $0; next }
        { print "- " $0 }'
    echo
  done <"$tmp"
} >"$out"
echo "wrote $out ($(grep -c '^## ' "$out") requirements)" >&2
