-- cone: branches a constant condition rules out, and everything below them.
-- A statement under any of these is in the design and cannot run at this
-- parameterisation; the arc is still reported, marked unreachable.
WITH RECURSIVE dead(branch_id) AS (
    SELECT branch_id FROM v_branch WHERE static_taken = 0
  UNION
    SELECT b.branch_id
    FROM v_branch b
    JOIN dead d ON b.parent_branch_id = d.branch_id
)
SELECT branch_id FROM dead
