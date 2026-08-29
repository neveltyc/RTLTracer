-- trace: quote one source line, and know whether the file still hashes to
-- what the export recorded. The wrapper reads the file at src_path and
-- compares to sha256 before quoting.
SELECT f.path AS file_path, sf.path AS src_path, sf.digest AS sha256
FROM file f
JOIN src_file sf ON sf.id = f.src_file_id
WHERE f.path = :file_path;
