-- trace: the dependency kind behind one dep_id. Per-row lookup, never a join.
SELECT dep_kind
FROM net_dep
WHERE id = :dep_id;
