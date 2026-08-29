-- resolve: nets of one instance, for a correction hint.
SELECT net_name
FROM v_net
WHERE inst_id = :inst_id
ORDER BY net_name
