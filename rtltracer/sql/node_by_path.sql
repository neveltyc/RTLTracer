-- tree: a tree level by its assembled path, when the scope is not a net.
SELECT node_id, node_path
FROM v_node_path
WHERE node_path = :node_path
