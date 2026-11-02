-- Hybrid evidence search — the ONE copy (docs/adr/0004, docs/adr/0006). Read by scripts/evidence.sh
-- locally and by the evidence-refresh routine in the cloud, so the two can never rank differently.
-- psql variables: :'qv' query vector literal ('search_query: ' prefix, scripts/embed.py query),
-- :'q' query text, :'scope' source_class or '', :'types' text-array literal of doc_types,
-- :k rows, and :cols the select list over d (career_docs), f.s (the fused score) and q.v (the query
-- vector), interpolated raw — the caller picks it; evidence.sh asks for doc_type, title, company,
-- repo [, content]; the routine asks for the full content_hash and the cosine 1 - (d.embedding <=> q.v).
-- Reference q.v, never :'qv' again: the vector literal is ~5–8 KB and the cloud routine passes the
-- whole statement through a connector call.
-- Reciprocal-rank fusion (k = 60) of the top 50 by vector distance and the top 50 by full-text
-- rank (any query word, English stemming): nDCG@10 +0.104 over vector-only, 95% CI [+0.051, +0.159].
WITH q AS MATERIALIZED (SELECT :'qv'::vector AS v),
     tq AS MATERIALIZED (SELECT to_tsquery('english', coalesce(nullif(array_to_string(
            tsvector_to_array(to_tsvector('english', :'q')), ' | '), ''), 'xxnomatchxx')) AS t),
     pool AS (SELECT * FROM public.career_docs
              WHERE (:'scope' = '' OR source_class = :'scope') AND doc_type = ANY(:'types'::text[])),
     v AS (SELECT p.id, row_number() OVER (ORDER BY p.embedding <=> q.v) AS r
           FROM pool p, q ORDER BY p.embedding <=> q.v LIMIT 50),
     b AS (SELECT p.id, row_number() OVER (ORDER BY ts_rank_cd(p.tsv, tq.t) DESC) AS r
           FROM pool p, tq WHERE p.tsv @@ tq.t ORDER BY ts_rank_cd(p.tsv, tq.t) DESC LIMIT 50),
     f AS (SELECT id, sum(1.0 / (60 + r)) AS s FROM (SELECT * FROM v UNION ALL SELECT * FROM b) u GROUP BY id)
SELECT :cols
FROM f JOIN public.career_docs d USING (id) CROSS JOIN q
ORDER BY f.s DESC
LIMIT :k;
