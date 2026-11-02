#!/usr/bin/env bash
# render.sh <application-folder> [role-title]
# Validate + render an application's resume.json, then name the submitted PDF
#   <FullNameInPascalCase>-<RoleTitleInPascalCase>.pdf   (e.g. JaneDoe-StaffEngineer.pdf)
# FullName comes from basics.name in the folder's resume.json; RoleTitle from [role-title]
# (defaults to the folder's basename). Enforces the repo's submitted-PDF naming convention.
set -euo pipefail

usage() {
  echo "usage: scripts/render.sh <application-folder> [role-title]" >&2
  # shellcheck disable=SC2016 # a literal $JOBSEEK_DRIVE_DIR is the point of the example
  echo '  e.g. scripts/render.sh "$JOBSEEK_DRIVE_DIR/applications/acme-corp/staff-engineer" "Staff Engineer"' >&2
  exit 2
}
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage

for bin in jsonresume jq; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "error: '$bin' not found on PATH" >&2
    exit 1
  }
done

dir="${1%/}"
resume="$dir/resume.json"
[ -f "$resume" ] || {
  echo "error: no resume.json in $dir" >&2
  exit 1
}

# Role title: explicit arg, else the folder's basename (the role slug).
role_raw="${2:-$(basename "$dir")}"

# PascalCase: strip non-alphanumerics to spaces, capitalize each word, join.
pascal() {
  printf '%s' "$1" |
    sed -E 's/[^A-Za-z0-9]+/ /g' |
    awk '{for(i=1;i<=NF;i++) printf "%s", toupper(substr($i,1,1)) substr($i,2)}'
}

name_raw="$(jq -r '.basics.name // ""' "$resume")"
[ -n "$name_raw" ] || {
  echo "error: basics.name is empty in $resume — populate it first" >&2
  exit 1
}

full="$(pascal "$name_raw")"
role="$(pascal "$role_raw")"
[ -n "$full" ] && [ -n "$role" ] || {
  echo "error: empty name/role after PascalCasing" >&2
  exit 1
}
out="$dir/$full-$role.pdf"

# Validate before rendering.
jsonresume validate --path "$resume"

# Render from a copy in which every open role (startDate, no endDate) carries endDate: null.
# The schema rejects null (and ""), so the stored file omits endDate; but the theme's
# formatDateRange (jsonresume.org packages/utils/src/dates.js) renders an ABSENT endDate as a
# single date and only an explicit null as "<start> - Present". Without this the current job
# reads "Mar 2026" — to an ATS, a one-month role. final-check.py asserts the "Present".
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
jq '(.work // []) |= map(if has("startDate") and (has("endDate") | not) then .endDate = null else . end)' \
  "$resume" >"$tmp/resume.json"

# Render (jsonresume print emits resume.pdf into --destination), then name it.
jsonresume print --path "$tmp/resume.json" --destination "$tmp"
mv -f "$tmp/resume.pdf" "$out"

echo "rendered $out"
