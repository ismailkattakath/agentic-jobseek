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
# The connection reaches psql through its ENVIRONMENT (PGHOST, PGPORT, PGUSER, PGPASSWORD,
# PGDATABASE, PGSSLMODE …), never as an argument: `psql "$JOBSEEK_DB_URI"` put the password in argv,
# which every local process reads through `ps` (measured 2026-11-02). Run `psql -X …` with no
# connection argument after sourcing this file. scripts/embed.py pgenv does the one parse, and
# drops Neon's "-pooler" from the host: scripts connect DIRECTLY, because the pooler's transaction
# mode drops temp tables between transactions (evidence-sync.sh stages rows in one) and leaked a
# dump's search_path into the next client (measured 2026-11-02).
JOBSEEK_DB_URI="${JOBSEEK_DB_URI/-pooler./.}" # a caller still using the variable gets the direct host too
_pgenv="$(python3 -I "$(dirname "${BASH_SOURCE[0]}")/embed.py" pgenv)" || exit 1
eval "$_pgenv"
unset _pgenv
