-- cone (BFS engine): driving arcs of a set of nets, one batch per walk
-- level. The view is presented traversal-oriented: the frontier net is
-- `near`, the edge leads `near -> far`.
SELECT d.edge_source,
       d.edge_id,
       d.src_net_id, d.dst_net_id,
       d.dst_net_id AS near_net_id, d.src_net_id AS far_net_id,
       d.dst_lo AS near_lo, d.dst_hi AS near_hi,
       d.src_lo AS far_lo, d.src_hi AS far_hi,
       d.map_kind, d.edge_kind,
       d.dep_id, d.stmt_id, d.branch_id,
       d.call_site_id,
       d.file_path, d.src_line
FROM v_trace_edge d
WHERE d.dst_net_id IN ({nets})
  AND d.src_net_id IS NOT NULL
