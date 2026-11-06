-- The full text of matched evidence versions, for the drafter briefs (scripts/prepare.py,
-- ADR-0006 § 4j). The ONE copy: the routine used to hand-write this query each run, and two runs
-- got it wrong (a uuid compared with text; column names written as values).
-- :hashes is a text-array literal of 64-hex content hashes, checked by the caller before
-- interpolation. Rows are versions (primary key content_hash).
SELECT doc_id::text AS doc_id, content_hash, status, title, content, repo, period, doc_type,
       source_class, company
FROM public.evidence
WHERE content_hash = ANY(:hashes::text[])
ORDER BY content_hash;
