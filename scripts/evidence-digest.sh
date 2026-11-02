#!/usr/bin/env bash
# evidence-digest.sh — SessionStart backlog digest (docs/adr/0007 § 5).
#
# Prints one or two lines: pending count, oldest pending age, rejected count, and the
# newest discovery run's until_at. Local files only: no Neon, no network, no jq-heavy
# work, so it stays well under 200 ms.
#
# Never fails the session: it always exits 0, and prints nothing when JOBSEEK_DRIVE_DIR
# is unset or its evidence folder is missing.

# Deliberately no `set -e`: any failing probe must degrade to "say less", not to a
# non-zero exit that would break the session start.
set +e
trap 'exit 0' EXIT

# JOBSEEK_DRIVE_DIR normally arrives through direnv (.envrc.local). A SessionStart hook
# may not inherit direnv's environment, so read that one assignment from the file. It
# is parsed with grep, never sourced, so the file's other lines never execute here.
DIR="${JOBSEEK_DRIVE_DIR:-}"
if [ -z "$DIR" ]; then
  ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)" || exit 0
  LINE="$(grep -m1 '^export JOBSEEK_DRIVE_DIR=' "$ROOT/.envrc.local" 2>/dev/null)" || exit 0
  DIR="${LINE#export JOBSEEK_DRIVE_DIR=}"
  DIR="${DIR#\"}"; DIR="${DIR%\"}"
  DIR="${DIR#\'}"; DIR="${DIR%\'}"
fi
[ -n "$DIR" ] || exit 0

# Expand a literal $HOME left in the value (the .envrc.local value uses "$HOME/...").
# shellcheck disable=SC2016  # the literal "$HOME" text is the pattern on purpose
case "$DIR" in
  *'$HOME'*) DIR="${DIR//\$HOME/$HOME}" ;;
esac

E="$DIR/evidence"
[ -d "$E" ] || exit 0

PENDING="$E/pending.jsonl"
REJECTED="$E/rejected.jsonl"

# Count non-blank lines. grep -c counts a final line with no trailing newline, which
# wc -l would silently drop.
count_lines() {
  [ -f "$1" ] || { echo 0; return; }
  n="$(grep -c . "$1" 2>/dev/null)"
  echo "${n:-0}"
}
N_PENDING="$(count_lines "$PENDING")"
N_REJECTED="$(count_lines "$REJECTED")"

# Convert a stamp to epoch seconds. Accepts the proposal stamp forms (2026-11-02,
# 20261102T1606Z) and an ISO created_at (2026-11-02T16:06:24Z). Prints nothing if unparsed.
to_epoch() {
  local s="$1" e=""
  case "$s" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*) s="${s%%.*}"; s="${s%Z}"
      e="$(date -j -u -f '%Y-%m-%dT%H:%M:%S' "$s" +%s 2>/dev/null || date -u -d "$s" +%s 2>/dev/null)" ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
      e="$(date -j -u -f '%Y-%m-%d' "$s" +%s 2>/dev/null || date -u -d "$s" +%s 2>/dev/null)" ;;
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9]Z)
      e="$(date -j -u -f '%Y%m%dT%H%MZ' "$s" +%s 2>/dev/null || date -u -d "${s:0:4}-${s:4:2}-${s:6:2}T${s:9:2}:${s:11:2}:00Z" +%s 2>/dev/null)" ;;
  esac
  [ -n "$e" ] && echo "$e"
}

# Oldest pending age in whole days. Prefers metadata.proposal.stamp, else created_at.
OLDEST_AGE=""
if [ "$N_PENDING" -gt 0 ] && command -v jq >/dev/null 2>&1; then
  OLDEST_EPOCH=""
  while IFS= read -r stamp; do
    ep="$(to_epoch "$stamp")"
    [ -n "$ep" ] || continue
    if [ -z "$OLDEST_EPOCH" ] || [ "$ep" -lt "$OLDEST_EPOCH" ]; then OLDEST_EPOCH="$ep"; fi
  done < <(jq -r '(.metadata.proposal.stamp // .created_at // empty)' "$PENDING" 2>/dev/null)
  if [ -n "$OLDEST_EPOCH" ]; then
    NOW="$(date +%s)"
    OLDEST_AGE=$(( (NOW - OLDEST_EPOCH) / 86400 ))
  fi
fi

# Newest discovery run: the largest until_at across run-*.json in the inbox folders.
# until_at is ISO-8601 UTC, so a lexicographic sort is a chronological sort.
LAST_RUN=""
if command -v jq >/dev/null 2>&1; then
  LAST_RUN="$(find "$E/inbox" -maxdepth 2 -name 'run-*.json' 2>/dev/null \
    | while IFS= read -r f; do jq -r '.until_at // empty' "$f" 2>/dev/null; done \
    | sort | tail -1)"
fi

# Line 1: backlog. Line 2: last discovery run. Output stays two lines.
if [ -n "$OLDEST_AGE" ]; then
  echo "evidence: $N_PENDING pending (oldest ${OLDEST_AGE}d), $N_REJECTED rejected"
else
  echo "evidence: $N_PENDING pending, $N_REJECTED rejected"
fi
if [ -n "$LAST_RUN" ]; then
  echo "evidence: last discovery run until $LAST_RUN"
else
  echo "evidence: no discovery run found in inbox/"
fi
exit 0
