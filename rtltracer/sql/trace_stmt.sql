-- trace: one statement, for the arcs that name it.
SELECT stmt_id, inst_id, module_id, module_name, scope_node_id, proc_id,
       ordinal, sequence, stmt_kind, construct, assign_kind, delay,
       dropped_operand_count, call_site_id, branch_id,
       file_path, src_path, src_line, src_col
FROM v_stmt
WHERE stmt_id = :stmt_id;
