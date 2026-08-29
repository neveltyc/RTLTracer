-- info: every source file the export read, its absolute path and SHA-256.
-- The Python wrapper hashes the file on disk and compares digests.
SELECT f.path AS file_path, sf.path AS src_path, sf.digest AS sha256
FROM file f
JOIN src_file sf ON sf.id = f.src_file_id
ORDER BY sf.path;
