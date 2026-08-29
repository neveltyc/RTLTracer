"""RTLTracer — signal trace, driver and load analysis over an rtl-designdb database.

Queries read the RTLDebugDBKit v20 schema: its v_* views where they compose the
answer, and base tables (net_dep, branch_ancestor, and the like) where a view
would fan out or the fact is not one a view exposes. Python is a thin CLI
wrapper; the SQL is the contract.
"""
__version__ = "0.1.0"
