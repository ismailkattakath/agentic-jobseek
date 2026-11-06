#!/usr/bin/env bash
# drop-deploy.sh — push apps-script/inbox-drop/ to its Apps Script project and redeploy the ONE
# web-app deployment, so the URL in the cloud environment never changes (ADR-0006 § 4h, § 4l).
#
#   scripts/drop-deploy.sh            # push + redeploy
#   scripts/drop-deploy.sh --dry-run  # resolve the project and deployment, change nothing
#
# Runs from the operator's Mac with their clasp login (~/.clasprc.json); the web app executes as
# whoever deploys it, so a CI identity would change the Drive owner. No id lives in this repo:
# the project is found by name with `clasp list`, the deployment as its only versioned one
# (JOBSEEK_DROP_SCRIPT_ID / JOBSEEK_DROP_DEPLOYMENT_ID override). A new OAuth scope in
# appsscript.json still needs the operator's consent once: run `setup` in the script editor.
set -euo pipefail

CLASP=(npx --yes @google/clasp@3.4.1) # pinned: the version this was verified with
NAME="evidence inbox drop"
here=$(cd "$(dirname "$0")/.." && pwd)
src="$here/apps-script/inbox-drop"
dry=${1:-}

id=${JOBSEEK_DROP_SCRIPT_ID:-$("${CLASP[@]}" list --noShorten | sed -n "s|^$NAME  *- https://script.google.com/d/\([A-Za-z0-9_-]*\)/edit$|\1|p")}
[ "$(printf '%s\n' "$id" | grep -c .)" -eq 1 ] || {
  echo "drop-deploy: expected one project named '$NAME', found: ${id:-none}" >&2
  exit 1
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp "$src/Code.gs" "$src/appsscript.json" "$work/"
printf '{"scriptId":"%s","rootDir":"."}\n' "$id" >"$work/.clasp.json"
cd "$work"

dep=${JOBSEEK_DROP_DEPLOYMENT_ID:-$("${CLASP[@]}" deployments | sed -n 's/^- \(AKfy[A-Za-z0-9_-]*\) @[0-9][0-9]* .*/\1/p')}
[ "$(printf '%s\n' "$dep" | grep -c .)" -eq 1 ] || {
  echo "drop-deploy: expected one versioned deployment, found $(printf '%s\n' "$dep" | grep -c .)" >&2
  exit 1
}
echo "drop-deploy: project ${id:0:8}…, deployment ${dep:0:10}…"
[ "$dry" = "--dry-run" ] && exit 0

"${CLASP[@]}" push -f
rev=$(git -C "$here" rev-parse --short HEAD)
"${CLASP[@]}" deploy -i "$dep" -d "evidence inbox drop @ $rev" | sed -E 's/(AKfy[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+/\1…/g'
