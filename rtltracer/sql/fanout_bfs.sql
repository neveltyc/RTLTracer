-- cone (BFS engine): reading arcs of a set of nets, one batch per walk
-- level. {nets} is expanded by the wrapper to ?,?,... — the point query
-- itself seeks net_dep_by_src.
SELECT d.signal_net_id AS src_net_id,
       d.load_net_id   AS tgt_net_id,
       d.load_kind,
       (SELECT dd.dep_kind FROM net_dep dd WHERE dd.id = d.dep_id) AS dep_kind,
       d.dep_id, d.conn_id, d.stmt_id, d.proc_id, d.term_id,
       d.map_exact, d.call_site_id, d.branch_id,
       d.file_path, d.src_path, d.src_line, d.src_col
FROM v_load d
WHERE d.signal_net_id IN ({nets})
  AND d.load_net_id IS NOT NULL
  AND (:no_ctl = 0 OR COALESCE((SELECT dd.dep_kind FROM net_dep dd WHERE dd.id = d.dep_id), '') <> 'control')
