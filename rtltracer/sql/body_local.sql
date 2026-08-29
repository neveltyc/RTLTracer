-- cone: nets that are subroutine body locals (formals and locals). One call
-- shares them with another, so the walk must keep calls apart there. Read
-- once for the design, never per net.
--
-- A subroutine name carries '_', a LIKE metacharacter, so matched literally
-- by prefix rather than as a pattern.
SELECT DISTINCT n.id AS net_id
FROM net n
JOIN call_site cs ON cs.inst_id = n.inst_id
WHERE substr(n.name, 1, length(cs.subroutine_name) + 1) = cs.subroutine_name || '.'
