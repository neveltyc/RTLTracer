-- find: definitions whose name matches a glob, with occurrence counts.
SELECT m.name,
       m.def_kind,
       COUNT(i.id) AS occurrences
FROM module m
LEFT JOIN inst i ON i.module_id = m.id
WHERE m.name GLOB :pattern
GROUP BY m.id
ORDER BY m.name
LIMIT :limit;
