-- resolve: what is at one tree level, for a correction hint.
SELECT node_name
FROM v_tree_node
WHERE parent_node_id = :parent
ORDER BY node_name
