-- cone (BFS engine): driving arcs of a set of nets, one batch per walk
-- level. {nets} is expanded by the wrapper to ?,?,... — the point query
-- itself seeks net_dep_by_tgt, so the closure stays index-fast.
SELECT d.driver_net_id AS src_net_id,
       d.signal_net_id AS tgt_net_id,
       d.driver_kind,
       (SELECT dd.dep_kind FROM net_dep dd WHERE dd.id = d.dep_id) AS dep_kind,
       d.dep_id, d.conn_id, d.stmt_id, d.prim_id, d.term_id,
       d.map_exact, d.call_site_id,
       d.file_path, d.src_path, d.src_line, d.src_col
FROM v_driver d
WHERE d.signal_net_id IN ({nets})
  AND d.driver_net_id IS NOT NULL
  AND (:no_ctl = 0 OR d.driver_kind <> 'control')
