-- cone: nets that are subroutine body locals (formals and locals). One call
-- shares them with another, so the walk must keep calls apart there. Read
-- once for the design, never per net.
SELECT DISTINCT n.id AS net_id
FROM net n
JOIN call_site cs ON cs.inst_id = n.inst_id
WHERE n.name LIKE cs.subroutine_name || '.%'
