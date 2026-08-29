-- resolve: one path segment down the elaborated tree.
SELECT node_id, node_name, node_kind, inst_id, parent_node_id
FROM v_tree_node
WHERE parent_node_id = :parent AND node_name = :name
