-- path: name and locate one driving arc between two adjacent nets on a route.
SELECT driver_kind, dep_id, file_path, src_line
FROM v_driver
WHERE signal_net_id = :signal_net_id AND driver_net_id = :driver_net_id
LIMIT 1
