-- trace: every node's assembled path. v_node_path walks the whole tree per
-- query, so it is taken once and kept, never looked up per hop.
SELECT node_id, node_path
FROM v_node_path
