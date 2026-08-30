-- cone (BFS engine): driving arcs of a set of nets, one batch per walk
-- level. {nets} is expanded by the wrapper to ?,?,... — the point query
-- itself seeks net_dep_by_tgt, so the closure stays index-fast.
SELECT d.driver_net_id AS src_net_id,
       d.signal_net_id AS tgt_net_id,
       d.driver_kind,
       (SELECT dd.dep_kind FROM net_dep dd WHERE dd.id = d.dep_id) AS dep_kind,
       d.dep_id, d.conn_id, d.stmt_id, d.prim_id, d.term_id,
       d.map_exact, d.call_site_id,
       -- bit windows: cur_* is the frontier (driven) net, other_* the driver.
       d.signal_lo AS cur_lo, d.signal_hi AS cur_hi, d.signal_exact AS cur_exact,
       d.driver_lo AS other_lo, d.driver_hi AS other_hi, d.driver_exact AS other_exact,
       d.file_path, d.src_path, d.src_line, d.src_col
FROM v_driver d
WHERE d.signal_net_id IN ({nets})
  AND d.driver_net_id IS NOT NULL
  AND (? = 0 OR d.driver_kind <> 'control')
