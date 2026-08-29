-- resolve: a tree node's instance, by node id.
SELECT node_id, inst_id
FROM v_tree_node
WHERE node_id = :node_id
