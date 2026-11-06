-- Hybrid evidence search — the ONE copy (docs/adr/0004, docs/adr/0006, docs/adr/0007). Read by
-- scripts/evidence.sh locally, by scripts/retrieval-eval.py, and by the evidence-discover routine in the
-- cloud, so they can never rank differently.
-- psql variables: :'qv' query vector literal ('search_query: ' prefix, scripts/embed.py query),
-- :'q' query text, :'scope' source_class or '', :'types' text-array literal of doc_types,
-- :'statuses' text-array literal of states to search ('{approved}' is the default scripts/sql-render.py
-- applies; '{approved,pending}' for tailoring, all three for the routine's dedupe — ADR-0007 § 2.4, § 2.6),
-- :k rows, and :cols the select list over d (the evidence table), f.s (the fused score) and q.v
-- (the query vector), interpolated raw — the caller picks it; evidence.sh asks for doc_type, title,
-- company, repo [, content]; the routine asks for doc_id, the full content_hash, status and the
-- cosine 1 - (d.embedding <=> q.v).
-- Reference q.v, never :'qv' again: the vector literal is ~5–8 KB and the cloud routine passes the
-- whole statement through a connector call.
-- Rows are VERSIONS (primary key content_hash): a pending enrichment shares its approved base's doc_id,
-- so both can appear; the caller groups by d.doc_id.
-- Reciprocal-rank fusion (k = 60) of the top 50 by vector distance and the top 50 by full-text
-- rank (any query word, English stemming): nDCG@10 +0.104 over vector-only, 95% CI [+0.051, +0.159].
WITH q AS MATERIALIZED (SELECT :'qv'::vector AS v),
     -- Each lexeme is quote_literal()'d: a raw one such as 'github.com/x)' is not tsquery syntax and
     -- failed the whole search (2026-11-06, a query holding a URL).
     tq AS MATERIALIZED (SELECT to_tsquery('english', coalesce(nullif(array_to_string(
            ARRAY(SELECT quote_literal(x) FROM unnest(tsvector_to_array(to_tsvector('english', :'q'))) x),
            ' | '), ''), 'xxnomatchxx')) AS t),
     pool AS (SELECT * FROM public.evidence
              WHERE (:'scope' = '' OR source_class = :'scope') AND doc_type = ANY(:'types'::text[])
                AND status = ANY(:'statuses'::text[])),
     v AS (SELECT p.content_hash, row_number() OVER (ORDER BY p.embedding <=> q.v) AS r
           FROM pool p, q ORDER BY p.embedding <=> q.v LIMIT 50),
     b AS (SELECT p.content_hash, row_number() OVER (ORDER BY ts_rank_cd(p.tsv, tq.t) DESC) AS r
           FROM pool p, tq WHERE p.tsv @@ tq.t ORDER BY ts_rank_cd(p.tsv, tq.t) DESC LIMIT 50),
     f AS (SELECT content_hash, sum(1.0 / (60 + r)) AS s
           FROM (SELECT * FROM v UNION ALL SELECT * FROM b) u GROUP BY content_hash)
SELECT :cols
FROM f JOIN public.evidence d USING (content_hash) CROSS JOIN q
ORDER BY f.s DESC
LIMIT :k;
