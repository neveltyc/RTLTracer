-- resolve: a net declared in one instance, by its full local name
-- (generate and subroutine segments included).
SELECT net_id, inst_id, net_name, decl_kind, data_type, width,
       scope_node_id, file_path, src_path, src_line, src_col
FROM v_net
WHERE inst_id = :inst_id AND net_name = :name
