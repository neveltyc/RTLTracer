-- trace: the call string a row belongs to. Walked up parent_call_site_id
-- by the wrapper; depth is small.
SELECT call_site_id, parent_call_site_id, subroutine_name, depth
FROM v_call_site
WHERE call_site_id = :call_site_id;
