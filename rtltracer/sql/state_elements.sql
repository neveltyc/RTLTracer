-- cone: state elements — nets a combinational walk stops at, with
-- propagation across whole-width port ties.
--
-- Base set: nonblocking writes in an edge-sensitive procedure (clocked)
-- and writes in always_latch (latch). Then the property is carried across
-- every whole-width connection arc (src_lo/dst_lo both NULL): a flop's
-- output wired whole to a port makes the far net a state element too.
-- Half-width ties do not propagate.
--
-- Returns one row per state net: net_id + state_kind ('clocked'|'latch').
WITH state_kind(net_id, kind) AS (
    SELECT DISTINCT d.tgt_net_id,
           CASE WHEN p.proc_kind = 'always_latch' THEN 'latch' ELSE 'clocked' END
    FROM net_dep d
    JOIN stmt s ON s.id = d.stmt_id
    JOIN proc p ON p.id = s.proc_id
    WHERE d.dep_kind = 'data'
      AND (p.proc_kind = 'always_latch'
           OR (s.assign_kind = 'nonblocking'
               AND (p.proc_kind = 'always_ff'
                    OR (p.proc_kind = 'always' AND EXISTS(
                          SELECT 1 FROM proc_event e
                          WHERE e.proc_id = p.id
                            AND e.event_kind = 'sensitivity'
                            AND e.edge_kind IS NOT NULL)))))
),
prop(net_id, kind) AS (
    SELECT net_id, kind FROM state_kind
  UNION
    SELECT d.dst_net_id, p.kind
    FROM prop p
    JOIN v_trace_edge d ON d.src_net_id = p.net_id
    WHERE d.edge_kind = 'connection'
      AND d.src_lo IS NULL AND d.dst_lo IS NULL
  UNION
    SELECT d.src_net_id, p.kind
    FROM prop p
    JOIN v_trace_edge d ON d.dst_net_id = p.net_id
    WHERE d.edge_kind = 'connection'
      AND d.src_lo IS NULL AND d.dst_lo IS NULL
)
SELECT DISTINCT net_id, kind FROM prop
