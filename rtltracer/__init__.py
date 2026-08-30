"""RTLTracer — signal trace, driver and load analysis over an rtl-designdb database.

Queries read the RTLDebugDBKit v22 schema: its v_* views where they compose the
answer, and base tables where a fact is not one a view exposes. SQL performs
indexed fact lookup; Python implements traversal semantics.
"""
__version__ = "0.1.0"
