-- trace: the events one procedure triggers on.
SELECT proc_event_id, proc_id, proc_kind, net_id, net_name,
       event_kind, edge_kind, file_path, src_path, src_line, src_col
FROM v_proc_event
WHERE proc_id = :proc_id
  AND event_kind = 'sensitivity'
ORDER BY proc_event_id;
