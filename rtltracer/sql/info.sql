-- info: the export seal, one row.
SELECT schema_version, tool, tool_version, slang_version, producer_revision,
       top, analysis_status, error_count, unresolved_count,
       empty_procedure_count, duplicate_path_count, recursion_count,
       truncated_call_count, checker_inst_count, unanalysed_inst_count,
       config_digest
FROM v_db_info;
