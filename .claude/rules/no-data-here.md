# No data here — this repo holds procedures; every fact lives in Google Drive

Neither this repo nor its siblings (`agentic-nomad`, `agentic-ledger`) ever holds data.

**The test:** would this line read the same for a different person? Yes → procedure, it may
live here. No → data, it goes to its ONE home under `$JOBSEEK_DRIVE_DIR`
(`CLAUDE.md` § Where the data lives), never to a repo file.

| Fact | Its one home |
|---|---|
| Résumé content, employers, titles, dates, metrics | the master at `$JOBSEEK_MASTER_URL` (published, one copy) and each role's `resume.json` |
| A posting, research notes, a cover letter | the role folder |
| Where an application stands | the role folder's `status.md` |
| What work actually exists | `evidence/{approved,pending,rejected}.jsonl` — the pgvector table `evidence` is synced from them and holds no fact of its own |

This applies to **prompts and examples too**: a skill that illustrates a rule uses fabricated
values (`Jane Doe`, `Acme Corp`, `"<N> vendors"`), never a real employer, application or
metric. **Before writing a fact anywhere, check it is not already in its home** — a second
copy is a bug.

`.gitleaks.toml` is the mechanical floor; it catches shapes (phone numbers, secrets), not
prose — this rule is the rest. `.gitignore` blocks `*.pdf`, `*.jsonl` and `resume.json` as a
backstop, but the real protection is that the data never lives here.
