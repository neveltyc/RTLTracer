-- find: tree levels whose own segment matches a glob, under the root.
SELECT t.node_id,
       t.node_name,
       t.node_kind,
       t.module_name,
       t.def_name,
       np.node_path
FROM v_tree_node t
JOIN v_node_path np ON np.node_id = t.node_id
WHERE t.node_name GLOB :pattern
  AND (np.node_path = :root_path OR np.node_path LIKE :root_path || '.%')
ORDER BY np.node_path
LIMIT :limit;
