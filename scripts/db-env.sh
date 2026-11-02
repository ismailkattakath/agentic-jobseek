# shellcheck shell=bash
# db-env.sh — sourced (with the caller's "$@") by every script that reads or writes the evidence
# cache. The cache lives in Neon (docs/adr/0006) and its connection string holds a password, so it
# is never exported into the shell: JOBSEEK_DB_SECRET names the Keychain item, and the calling
# script re-runs under `secret exec`, which puts JOBSEEK_DB_URI in that one process only.
# Setting JOBSEEK_DB_URI directly (a local scratch database, say) skips the Keychain.
if [ -z "${JOBSEEK_DB_URI:-}" ]; then
  : "${JOBSEEK_DB_SECRET:?set JOBSEEK_DB_SECRET (the Keychain item holding the cache connection string) or JOBSEEK_DB_URI}"
  exec secret exec JOBSEEK_DB_URI="$JOBSEEK_DB_SECRET" -- "$0" "$@"
fi
# Scripts connect DIRECTLY, not through Neon's pooler (the stored URI's host ends in "-pooler"):
# the pooler's transaction mode drops temp tables between transactions (evidence-rebuild.sh stages
# rows in one) and leaked a dump's search_path into the next client (measured 2026-11-02).
JOBSEEK_DB_URI="${JOBSEEK_DB_URI/-pooler./.}"
