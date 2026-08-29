-- trace: every recorded reading arc of one net, one row each.
-- :ctl = 1 includes control (gating) reads; 0 leaves them out.
-- v_load carries control deps as dataflow, so the kind is looked up in
-- net_dep per row.
SELECT signal_net_id, signal_inst_id, signal_name, signal_ref,
       signal_lo, signal_hi, signal_exact,
       load_net_id, load_inst_id, load_name, load_ref,
       load_lo, load_hi, load_exact, load_kind,
       dep_id, conn_id, stmt_id, proc_id, term_id, map_exact,
       call_site_id, branch_id, file_path, src_path, src_line, src_col
FROM v_load
WHERE signal_net_id = :net_id
  AND (:ctl = 1 OR COALESCE((SELECT dep_kind FROM net_dep dd WHERE dd.id = dep_id), '') <> 'control')
