# Evidence gate — a claim enters a résumé only if the evidence store backs it

Content optimises the **presentation** of real experience. It never invents experience.

- **A bullet may be sharpened, quantified or added only if `scripts/evidence.sh` returns real
  backing for it.** Keep the backing row's title beside the edit.
- **An ATS keyword is accepted only if the store confirms it.** No backing → it is a genuine
  gap: record it in `status.md`, never add it.
- **Personal and open-source evidence proves a *skill*, never an enterprise *metric*.** A
  side project that shows a technique does not license "<N> vendors" or "~<N>% latency" on
  an employer's line.
- **A specification proves planning, never delivery.** A document with
  `metadata.evidence_kind: "specification"` (an issue the operator wrote) backs "specified",
  "designed" or "led the plan for" — never "built", "shipped" or a metric (ADR-0006 § 4e).
- **Never fabricate** an employer, title, date, degree, certification, metric or skill. Refuse
  even when asked to "just make it fit".
- **A `pending` row is not evidence.** Retrieval may show it (marked PENDING), but nothing
  it alone backs enters a résumé until the operator approves it (ADR-0007).
- **Scope:** `--scope enterprise` for a tight principal/staff narrative; all tiers for
  founding or applied-AI roles that reward the personal + OSS range.

**If the store is unreachable, STOP.** Never fall back silently to the master résumé alone:
an ungrounded tailor is exactly the failure this gate exists to prevent. `evidence.sh` itself
re-syncs a cache whose file hashes no longer match its Drive source (ADR-0007 § 4), and refuses to answer if that sync fails.
