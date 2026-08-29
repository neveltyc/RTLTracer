-- tree: depth-first levels under a root, with net and child counts.
-- :root_node_id selects the starting level; :max_depth bounds recursion.
WITH RECURSIVE walk(node_id, depth, path) AS (
    SELECT t.node_id, 0, t.node_name
    FROM v_tree_node t
    WHERE t.node_id = :root_node_id
  UNION ALL
    SELECT c.node_id, w.depth + 1, w.path || '.' || c.node_name
    FROM walk w
    JOIN v_tree_node c ON c.parent_node_id = w.node_id
    WHERE w.depth < :max_depth
)
SELECT w.depth,
       w.path,
       t.node_kind,
       t.module_name,
       t.def_name,
       (SELECT COUNT(*) FROM v_net n WHERE n.scope_node_id = w.node_id) AS nets,
       (SELECT COUNT(*) FROM v_tree_node c WHERE c.parent_node_id = w.node_id) AS children
FROM walk w
JOIN v_tree_node t ON t.node_id = w.node_id
ORDER BY w.path;
