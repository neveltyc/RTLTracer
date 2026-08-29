-- cone: the gating level a statement sits under, for dead-branch pruning.
SELECT branch_id
FROM v_stmt
WHERE stmt_id = :stmt_id
