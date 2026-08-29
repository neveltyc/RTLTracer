-- cone / trace: name a set of nets. {nets} is expanded to ?,?,...
SELECT n.net_id,
       n.inst_id,
       n.net_name,
       n.decl_kind,
       n.data_type,
       n.width,
       n.scope_node_id,
       np.node_path,
       np.node_path || '.' || n.net_name AS full_path
FROM v_net n
JOIN v_node_path np ON np.node_id = n.inst_id
WHERE n.net_id IN ({nets})
