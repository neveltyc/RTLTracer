-- trace: statements reachable from a set of arc rows, for metadata the arc
-- view does not carry (scope, procedure linkage, construct wording).
SELECT stmt_id, scope_node_id, proc_id, construct, stmt_kind,
       assign_kind, sequence, call_site_id,
       file_path, src_line
FROM v_stmt
WHERE stmt_id IN ({nets})
