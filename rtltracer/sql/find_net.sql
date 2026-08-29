-- find: nets whose own name matches a glob, anywhere under the chosen root.
-- :pattern is a SQLite GLOB pattern (*, ?); :root_path is the top's path.
SELECT n.net_id,
       n.inst_id,
       n.net_name,
       n.decl_kind,
       n.width,
       np.node_path,
       np.node_path || '.' || n.net_name AS full_path
FROM v_net n
JOIN v_node_path np ON np.node_id = n.inst_id
WHERE n.net_name GLOB :pattern
  AND (np.node_path = :root_path OR np.node_path LIKE :root_path || '.%')
ORDER BY full_path
LIMIT :limit;
