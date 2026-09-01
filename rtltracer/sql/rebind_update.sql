-- rebind: re-point one source file to a path whose content matches its digest.
UPDATE src_file SET path = :path WHERE id = :id;
