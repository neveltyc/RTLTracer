-- cone: statements a constant condition rules out. A statement whose gating
-- branch, or any ancestor of it, is statically not taken cannot run at this
-- parameterisation; its arcs are still reported, marked unreachable. The
-- gating branch lives on the statement (v_stmt.branch_id), not on the edge.
SELECT stmt_id
FROM v_stmt
WHERE branch_id IN (
    SELECT branch_id
    FROM branch_ancestor
    WHERE ancestor_branch_id IN (SELECT branch_id FROM v_branch WHERE static_taken = 0)
)
