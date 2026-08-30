-- cone (BFS engine): reading arcs of a set of nets, one batch per walk
-- level. {nets} is expanded by the wrapper to ?,?,...; the point query
-- itself seeks the edge view's source index, so the closure stays
-- index-fast.
SELECT d.edge_source,
       d.edge_id,
       d.src_net_id,
       d.dst_net_id,
       d.src_lo, d.src_hi,
       d.dst_lo, d.dst_hi,
       d.map_kind, d.edge_kind,
       d.dep_id, d.conn_arc_id, d.stmt_id, d.branch_id,
       d.call_site_id, d.prim_id,
       d.file_path, d.src_line, d.src_col
FROM v_trace_edge d
WHERE d.src_net_id IN ({nets})
  AND d.dst_net_id IS NOT NULL
  AND (? = 0 OR d.edge_kind <> 'control')
