-- trace: the gating chain of one statement, outermost first.
-- v20's branch_ancestor makes this one indexed lookup; reads come from
-- branch_ref per level.
SELECT v.depth,
       v.branch_kind,
       v.sense,
       v.case_kind,
       v.check_kind,
       v.static_taken,
       v.ordinal,
       v.labels,
       v.iter_name,
       v.iter_first,
       v.iter_step,
       v.iter_count,
       v.src_line,
       (SELECT group_concat(n.net_name)
          FROM branch_ref r JOIN v_net n ON n.net_id = r.net_id
         WHERE r.branch_id = v.branch_id) AS reads
FROM stmt s
JOIN branch_ancestor a ON a.branch_id = s.branch_id
JOIN v_branch v        ON v.branch_id = a.ancestor_branch_id
WHERE s.id = :stmt_id
ORDER BY v.depth;
