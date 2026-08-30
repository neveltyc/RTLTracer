-- trace: every recorded driving arc of one net, one row each, presented
-- traversal-oriented (near = the traced net, far = the other endpoint).
-- :ctl = 1 includes control (gating) arcs; 0 leaves them out.
SELECT v.signal_net_id AS net_id,
       v.signal_lo AS near_lo, v.signal_hi AS near_hi,
       v.driver_net_id AS far_net_id,
       v.driver_ref AS far_ref,
       v.driver_lo AS far_lo, v.driver_hi AS far_hi,
       CASE WHEN v.signal_exact = 1 AND v.driver_exact = 1 AND v.map_exact = 1
                 AND v.signal_lo IS NOT NULL AND v.signal_hi IS NOT NULL
                 AND v.driver_lo IS NOT NULL AND v.driver_hi IS NOT NULL
                 AND v.signal_hi - v.signal_lo = v.driver_hi - v.driver_lo
            THEN 'exact' ELSE 'inexact' END AS map_kind,
       v.driver_kind AS kind,
       d.dep_kind AS dep_kind,
       v.dep_id, v.conn_id, v.stmt_id, v.prim_id, v.term_id,
       v.call_site_id, v.file_path, v.src_path, v.src_line, v.src_col
FROM v_driver v
LEFT JOIN net_dep d ON d.id = v.dep_id
WHERE v.signal_net_id = :net_id
  AND (:ctl = 1 OR v.driver_kind <> 'control')
