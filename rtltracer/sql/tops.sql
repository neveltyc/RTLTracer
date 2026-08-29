-- resolve: the elaborated top(s), in id order.
SELECT t.node_id, t.node_name
FROM v_tree_node t
WHERE t.node_kind = 'root'
ORDER BY t.node_id;
