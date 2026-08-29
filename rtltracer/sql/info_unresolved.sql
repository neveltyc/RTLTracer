-- info: hierarchical references the export could not resolve, by access.
SELECT COALESCE(SUM(access = 'read'), 0)  AS unresolved_reads,
       COALESCE(SUM(access = 'write'), 0) AS unresolved_writes
FROM v_hier_ref
WHERE resolved_net_id IS NULL;
