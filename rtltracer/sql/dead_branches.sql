-- cone: branches a constant condition rules out, and everything below them.
-- A statement under any of these is in the design and cannot run at this
-- parameterisation; the arc is still reported, marked unreachable.
SELECT branch_id
FROM branch_ancestor
WHERE ancestor_branch_id IN (SELECT branch_id FROM v_branch WHERE static_taken = 0)
