-- trace: the procedure's own name for a proc_id.
SELECT proc_kind
FROM proc
WHERE id = :proc_id;
