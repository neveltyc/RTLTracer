"""End-to-end tests over a committed fixture database (tests/fixtures/sample.db,
built from sample.sv). Every command and every option is exercised through the
real CLI; assertions read the stable --json envelope, not the human text.

Run under pytest, or standalone without it:
    pytest tests/
    python tests/test_rtltracer.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = str(REPO / "tests" / "fixtures" / "sample.db")


def run(*args: str) -> tuple[int, str, str]:
    env = dict(os.environ, PYTHONPATH=str(REPO))
    p = subprocess.run([sys.executable, "-m", "rtltracer", *args],
                       cwd=REPO, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def j(*args: str) -> dict:
    code, out, err = run(*args, "--json")
    assert out, f"no stdout for {args}: {err}"
    return json.loads(out)


# --- info -------------------------------------------------------------------

def test_info():
    d = j("info", DB)
    assert d["status"] == "ok"
    assert d["data"]["schema_version"] == 20
    assert d["data"]["analysis"]["status"] == "complete"
    assert d["summary"]["sources"] >= 1


def test_info_human_exit_zero():
    code, out, _ = run("info", DB)
    assert code == 0 and "Schema:" in out


def test_version_gate_rejects_other_schema():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "v19.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE v_db_info(schema_version INT, tool TEXT, "
                    "tool_version TEXT, slang_version TEXT, producer_revision TEXT, "
                    "top TEXT, analysis_status TEXT, error_count INT, unresolved_count INT, "
                    "empty_procedure_count INT, duplicate_path_count INT, recursion_count INT, "
                    "truncated_call_count INT, checker_inst_count INT, unanalysed_inst_count INT, "
                    "config_digest TEXT)")
        con.execute("INSERT INTO v_db_info(schema_version) VALUES (19)")
        con.commit()
        con.close()
        d = j("info", str(path))
        assert d["status"] == "error"
        assert d["errors"][0]["code"] == "DB_UNREADABLE"
        assert "19" in d["errors"][0]["message"]


# --- tree -------------------------------------------------------------------

def test_tree_root():
    paths = [lv["path"] for lv in j("tree", DB)["data"]["levels"]]
    assert "top" in paths and "top.u_sub" in paths


def test_tree_scope_instance():
    d = j("tree", DB, "top.u_sub")
    assert d["data"]["root"] == "top.u_sub"
    assert len(d["data"]["levels"]) == 1   # u_sub is a leaf instance


def test_tree_scope_by_net_does_not_crash():
    # A net names the instance that declares it; the walk roots there.
    code, _, _ = run("tree", DB, "top.q")
    assert code == 0


def test_tree_depth_and_limit():
    assert len(j("tree", DB, "--depth", "1")["data"]["levels"]) == 2
    s = j("tree", DB, "--limit", "1")["summary"]
    assert s["shown"] == 1 and s["truncated"] is True


# --- find -------------------------------------------------------------------

def test_find_nets():
    paths = [h["path"] for h in j("find", DB, "q")["data"]["hits"]]
    assert "top.q" in paths


def test_find_instances():
    paths = [h["path"] for h in j("find", DB, "u_*", "--instances")["data"]["hits"]]
    assert "top.u_sub" in paths


def test_find_modules():
    names = [h["path"] for h in j("find", DB, "*", "--modules")["data"]["hits"]]
    assert {"top", "sub"} <= set(names)


def test_find_truncation():
    d = j("find", DB, "*", "--limit", "1")
    assert len(d["data"]["hits"]) == 1 and d["summary"]["truncated"] is True


# --- trace ------------------------------------------------------------------

def test_trace_driver_boundary():
    d = j("trace", DB, "top.q")["data"]
    assert d["direction"] == "driver" and d["status"] == "boundary_only"


def test_trace_internal_register():
    d = j("trace", DB, "top.u_sub.dout")["data"]
    assert d["status"] == "resolved"
    assert any(h.get("timing") for h in d["hops"])   # always_ff clocking
    assert any(h.get("gates") for h in d["hops"])    # if (rst) gating


def test_trace_load():
    assert j("trace", DB, "top.a", "--load")["data"]["direction"] == "load"


def test_trace_ctl_adds_control_reads():
    plain = len(j("trace", DB, "top.muxed")["data"]["hops"])
    ctl = len(j("trace", DB, "top.muxed", "--ctl")["data"]["hops"])
    assert ctl > plain


def test_trace_top_option():
    assert j("trace", DB, "top.q", "--top", "top")["status"] == "ok"


def test_trace_bit_select():
    assert j("trace", DB, "top.q[3:0]")["data"]["bits"] == "[3:0]"


def test_trace_bad_select():
    code, _, _ = run("trace", DB, "top.q[99]")
    assert code == 1
    assert j("trace", DB, "top.q[99]")["errors"][0]["code"] == "BAD_SELECT"


def test_trace_testbench_prefix_discarded():
    assert j("trace", DB, "tb.dut.top.q")["data"]["signal"] == "top.q"


# --- fanin (transitive backward cone) ---------------------------------------

def test_fanin_is_transitive():
    # Regression: the walk must reach the leaf operands, not stop at depth 1.
    d = j("fanin", DB, "top.q", "--depth", "0")
    names = {n["path"].split(".")[-1] for n in d["data"]["nodes"]}
    assert {"a", "b"} <= names
    assert d["summary"]["max_depth_reached"] >= 3


def test_fanin_depth_bounds():
    one = j("fanin", DB, "top.q", "--depth", "1")["summary"]
    full = j("fanin", DB, "top.q", "--depth", "0")["summary"]
    assert one["max_depth_reached"] == 1
    assert full["nodes"] > one["nodes"]


def test_fanin_comb_stops_at_state():
    d = j("fanin", DB, "top.q", "--depth", "0", "--comb")["summary"]
    assert d["stopped_at_state"] >= 1
    assert d["nodes"] < j("fanin", DB, "top.q", "--depth", "0")["summary"]["nodes"]


def test_fanin_through_latch_crosses():
    comb = j("fanin", DB, "top.ql", "--depth", "0", "--comb")["summary"]["nodes"]
    thru = j("fanin", DB, "top.ql", "--depth", "0", "--comb", "--through-latch")["summary"]["nodes"]
    assert thru > comb


def test_fanin_no_ctl_drops_control():
    assert j("fanin", DB, "top.q", "--depth", "0", "--no-ctl")["summary"]["control_edges"] == 0


def test_fanin_follow_ctl_and_ctl_depth():
    base = j("fanin", DB, "top.q", "--depth", "0")["summary"]["nodes"]
    assert j("fanin", DB, "top.q", "--depth", "0", "--follow-ctl")["summary"]["nodes"] >= base
    assert j("fanin", DB, "top.q", "--depth", "0", "--ctl-depth", "1")["summary"]["nodes"] >= base


# --- fanout -----------------------------------------------------------------

def test_fanout_transitive():
    assert j("fanout", DB, "top.mode", "--depth", "0")["summary"]["nodes"] > 1


def test_fanout_depth_bounds():
    one = j("fanout", DB, "top.mode", "--depth", "1")["summary"]["nodes"]
    full = j("fanout", DB, "top.mode", "--depth", "0")["summary"]["nodes"]
    assert full >= one


# --- path -------------------------------------------------------------------

def test_path_found():
    d = j("path", DB, "top.a", "top.q")["data"]
    assert d["found"] is True and d["length"] == 4


def test_path_not_found():
    assert j("path", DB, "top.q", "top.a")["data"]["found"] is False


def test_path_comb_blocks_through_flop():
    assert j("path", DB, "top.a", "top.q", "--comb")["data"]["found"] is False


def test_path_option_smoke():
    for extra in (["--no-ctl"], ["--follow-ctl"], ["--ctl-depth", "1"],
                  ["--comb", "--through-latch"], ["--depth", "10"]):
        code, _, _ = run("path", DB, "top.a", "top.q", *extra)
        assert code == 0


# --- envelope / exit codes --------------------------------------------------

def test_json_envelope_shape():
    d = j("info", DB)
    assert {"tool", "version", "status", "command", "data", "summary"} <= set(d)


def test_unknown_signal_exits_one():
    code, _, err = run("trace", DB, "top.does_not_exist")
    assert code == 1 and "error" in err.lower()


def _run_standalone() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
