-- trace: every statement of one procedure that writes one net, in
-- execution order. Two sources: a local lvalue (v_stmt_target) and an
-- outward write (hier_ref, access='write').
SELECT s.sequence,
       s.src_line,
       s.branch_id,
       s.call_site_id,
       t.lo,
       t.hi,
       t.is_exact
FROM v_stmt s
JOIN v_stmt_target t ON t.stmt_id = s.stmt_id
WHERE s.proc_id = :proc_id
  AND t.net_id = :net_id
  AND t.target_kind = 'written_by'
UNION ALL
SELECT s.sequence,
       s.src_line,
       s.branch_id,
       s.call_site_id,
       h.lo,
       h.hi,
       h.is_exact
FROM v_stmt s
JOIN hier_ref h ON h.stmt_id = s.stmt_id
WHERE s.proc_id = :proc_id
  AND h.resolved_net_id = :net_id
  AND h.access = 'write'
ORDER BY sequence;
