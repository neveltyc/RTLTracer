-- trace: the dependency kind behind one dep_id, plus the primitive kind
-- where the arc came from a gate. Per-row lookup, never a join.
SELECT d.dep_kind, p.prim_kind
FROM net_dep d
LEFT JOIN prim p ON p.id = d.prim_id
WHERE d.id = :dep_id;
