-- trace: every recorded driving arc of one net, one row each.
-- :ctl = 1 includes control (gating) arcs; 0 leaves them out.
SELECT signal_net_id, signal_inst_id, signal_name, signal_ref,
       signal_lo, signal_hi, signal_exact,
       driver_net_id, driver_inst_id, driver_name, driver_ref,
       driver_lo, driver_hi, driver_exact, driver_kind,
       dep_id, conn_id, stmt_id, prim_id, term_id, map_exact,
       call_site_id, file_path, src_path, src_line, src_col
FROM v_driver
WHERE signal_net_id = :net_id
  AND (:ctl = 1 OR driver_kind <> 'control')
