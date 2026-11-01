# ADR-0001 — Stack

**Status:** accepted, 2026-11-01
**Context:** a Claude Code project for a job search — discover, tailor, track. It replaces an
earlier private repo that kept the data beside the code; this one keeps only procedures
(ADR-0002). Modelled on `agentic-nomad` and `agentic-ledger`.

## Decision in one line

**Reuse what is already installed at user scope; declare no servers here.** The value is the
evidence gate, the skills and the scripts, not a new stack.

## What this repo uses, and why it beat the alternative

| Job | Chosen | Beat | Why |
|---|---|---|---|
| Résumé format | **JSON Resume** (`@jsonresume/schema` v1.x) via the `jsonresume` wrapper over `resumed` | a Google Doc, LaTeX | A schema-validated file diffs, validates and re-renders per role; themes own the layout, so a variant changes words, never typography |
| Discovery | **`jobspy`** + the claude.ai **Indeed connector** | a custom scraper | Both already installed; `jobspy` covers several boards in one call |
| Evidence retrieval | **local pgvector** + in-DB `embed()` (nomic-embed-text on the local Ollama), via the `local-rag` setup the operator already runs | Vertex AI RAG Engine, Drive keyword search | Zero cost, on-device, already live. Vertex imports a Drive folder and reindexes changes, but is metered and would replace a working store. Drive search is keyword-only and misses paraphrase ("idempotent consumer" vs "exactly-once") |
| Evidence source of truth | **a JSONL file in Drive** | the pgvector table itself | The table is a cache (ADR-0002). A table on one machine with no source is a single copy |
| Posting structure | **schema.org `JobPosting`** (`posting.json`, filled from a page's JSON-LD) | a bespoke schema | It is the shape job boards already publish for Google |
| Pre-submit check | **poppler** (`pdfinfo`, `pdftotext`) in `scripts/final-check.py` | review agents alone | A 3-page overflow and zero-width characters in a PDF's text layer slipped past a 13-agent review on 2026-10-28; both are cheap to measure |
| Job-search craft | the global **ResumeSkills** (`job-description-analyzer`, `resume-ats-optimizer`, `cover-letter-generator`, `interview-prep-generator`, `salary-negotiation-prep`) | copies in this repo | Already installed; a copy here would be a second pin to drift |

## Rejected

- **A `.mcp.json` full of servers.** Everything above is user scope or a plugin.
- **A flake.** No compiled code, no hooks to test, and every tool is already on PATH.
  `agentic-nomad` has one for its hook tests; this repo has none.
- **A generated HTML dashboard.** The old repo vendored a design system to render one from
  `status.md`. A second copy of a design system to show eighteen rows is ceremony; if the
  pipeline outgrows `status.md`, the next step is a Google Sheet, not a dashboard.
- **Storing data in this repo.** See ADR-0002 and `rules/no-data-here.md`.
