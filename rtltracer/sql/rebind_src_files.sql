-- rebind: every source file the export recorded, by id, with its stored path
-- and content digest. The wrapper re-points path to a file that still hashes
-- to digest.
SELECT id, path, digest FROM src_file ORDER BY path;
