-- resolve: whether an instance declares a net of this name (a hint check).
SELECT 1
FROM v_net
WHERE inst_id = :inst_id AND net_name = :name
