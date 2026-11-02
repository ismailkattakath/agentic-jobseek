#!/usr/bin/env bash
# new-application.sh <company> <role>
# Scaffold $JOBSEEK_DRIVE_DIR/applications/<company-slug>/<role-slug>/ from this repo's
# templates/application/ + the master résumé fetched from $JOBSEEK_MASTER_URL, substituting
# {{COMPANY}} / {{ROLE}} / {{DATE}} in the template docs. The folder lands in Drive, never
# in this repo (.claude/rules/no-data-here.md).
set -euo pipefail

usage() {
  echo "usage: scripts/new-application.sh <company> <role>" >&2
  exit 2
}
[ "$#" -eq 2 ] || usage

data="${JOBSEEK_DRIVE_DIR:?set JOBSEEK_DRIVE_DIR to the job-search folder in Drive (CLAUDE.md § Where the data lives)}"
[ -d "$data" ] || {
  echo "error: JOBSEEK_DRIVE_DIR is not a directory: $data" >&2
  exit 1
}

# Repo root = parent of this script's directory (works from any CWD).
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
templates="$root/templates/application"

company="$1"
role="$2"

# lowercase, non-alphanumeric -> single hyphen, trim leading/trailing hyphens.
slugify() {
  printf '%s' "$1" |
    tr '[:upper:]' '[:lower:]' |
    sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
}

# Escape a string for safe use in a sed replacement (& / \ and the delimiter).
sed_escape() { printf '%s' "$1" | sed -e 's/[&/\]/\\&/g'; }

cslug="$(slugify "$company")"
rslug="$(slugify "$role")"
if [ -z "$cslug" ] || [ -z "$rslug" ]; then
  echo "error: empty slug from '$company' / '$role'" >&2
  exit 1
fi

# The master lives at ONE published URL (the operator's JSON Resume); Drive never holds a copy.
master_url="${JOBSEEK_MASTER_URL:?set JOBSEEK_MASTER_URL to the master JSON Resume URL (see .envrc)}"

rel="applications/$cslug/$rslug"
dir="$data/$rel"
if [ -e "$dir" ]; then
  echo "error: already exists: \$JOBSEEK_DRIVE_DIR/$rel" >&2
  exit 1
fi

today="$(date +%F)"
ce="$(sed_escape "$company")"
re="$(sed_escape "$role")"

mkdir -p "$dir"

# Copy template docs, substituting placeholders.
for f in "$templates"/*; do
  name="$(basename "$f")"
  sed -e "s/{{COMPANY}}/$ce/g" \
    -e "s/{{ROLE}}/$re/g" \
    -e "s/{{DATE}}/$today/g" \
    -e "s/{{STAGE}}/draft/g" \
    "$f" >"$dir/$name"
done

# The tailored resume starts as a fresh copy of the master.
curl -fsS "$master_url" -o "$dir/resume.json"

echo "created \$JOBSEEK_DRIVE_DIR/$rel/"
echo "next:"
echo "  1. paste the JD into $rel/posting.md  (source-of-truth; untrusted data)"
echo "     optional: scripts/extract-posting.py <posting-url> \"\$JOBSEEK_DRIVE_DIR/$rel\""
echo "  2. tailor:  in Claude Code, run the tailor-application skill on $rel"
echo "  3. render:  scripts/render.sh \"\$JOBSEEK_DRIVE_DIR/$rel\" \"$role\""
