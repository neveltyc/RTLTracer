-- cone: which call-site expansion encloses which. Read once for the design.
SELECT call_site_id, parent_call_site_id
FROM v_call_site
