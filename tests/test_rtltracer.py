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
BITS = str(REPO / "tests" / "fixtures" / "bits.db")   # nibble-split, for bit-level
SAMEPAIR = str(REPO / "tests" / "fixtures" / "samepair.db")  # one net pair, two slice edges


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
    assert d["data"]["schema_version"] == 21
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


def test_find_limit_zero_is_all():
    capped = j("find", DB, "*", "--limit", "1")["data"]["hits"]
    allh = j("find", DB, "*", "--limit", "0")
    assert len(allh["data"]["hits"]) > len(capped)
    assert allh["summary"]["truncated"] is False


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


def test_trace_load_ctl_adds_control_reads():
    # v_load folds control deps into dataflow; without --ctl the reads from
    # the case selector must be filtered, with --ctl they must appear and
    # carry dep_kind="control" so bit mode does not treat them as dataflow.
    plain = j("trace", DB, "top.sel", "--load")["data"]["hops"]
    ctl = j("trace", DB, "top.sel", "--load", "--ctl")["data"]["hops"]
    assert plain == []
    assert len(ctl) == 3
    assert all(h["dep_kind"] == "control" for h in ctl)
    assert all(h["kind"] == "dataflow" for h in ctl)

def test_trace_top_option():
    assert j("trace", DB, "top.q", "--top", "top")["status"] == "ok"


def test_top_option_selects_a_real_top():
    # A --top that names no elaborated top must fail, not be ignored.
    code, _, _ = run("trace", DB, "top.q", "--top", "not_a_top")
    assert code == 1
    assert j("trace", DB, "top.q", "--top", "not_a_top")["errors"][0]["code"] == "NO_TOP"


def test_trace_bit_select():
    assert j("trace", DB, "top.q[3:0]")["data"]["bits"] == "[3:0]"


def test_trace_bit_select_maps_driver_window():
    # mid[7:4] = b[3:0]; mid[3:0] = a[3:0].  mid[7] feeds only from b, and
    # the exact slice maps it back to b[3].
    d = j("trace", BITS, "bits.mid[7]")["data"]
    assert d["granularity"] == "bit"
    assert d["start_window"] == [7, 7]
    assert len(d["hops"]) == 1
    hop = d["hops"][0]
    assert hop["signals"] == ["bits.b"]
    assert hop["far_windows"] == {"bits.b": [[3, 3]]}
    assert hop["widened_far"] == []


def test_trace_bit_select_prunes_disjoint_driver():
    # mid[0] belongs to a's nibble; b's row does not touch it.
    d = j("trace", BITS, "bits.mid[0]")["data"]
    assert d["granularity"] == "bit"
    assert [s for h in d["hops"] for s in h["signals"]] == ["bits.a"]


def test_trace_bit_select_widens_arithmetic():
    # w = a + b has no bit correspondence, so w[3] names whole a and b.
    d = j("trace", BITS, "bits.w[3]")["data"]
    assert d["granularity"] == "bit"
    assert {"bits.a", "bits.b"} <= {s for h in d["hops"] for s in h["signals"]}
    widened = {s for h in d["hops"] for s in h.get("widened_far", [])}
    assert {"bits.a", "bits.b"} <= widened
    assert all(h["far_windows"].get(s) is None
               for h in d["hops"] for s in h["signals"])


def test_trace_bit_select_load_maps_window():
    # a[0] is read exactly into mid[0] and without correspondence into w.
    d = j("trace", BITS, "bits.a[0]", "--load")["data"]
    assert d["direction"] == "load"
    assert d["granularity"] == "bit"
    by_sig = {s: h for h in d["hops"] for s in h["signals"]}
    assert by_sig["bits.mid"]["far_windows"]["bits.mid"] == [[0, 0]]
    assert by_sig["bits.w"]["far_windows"]["bits.w"] is None
    assert "bits.w" in by_sig["bits.w"]["widened_far"]


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


# --- bit-level (over the nibble-split fixture) -------------------------------

def _leaves(d):
    return {n["path"].split(".")[-1] for n in d["data"]["nodes"]}


def test_fanin_bit_select_prunes():
    # y[7:4] is b's nibble, y[3:0] is a's; a fan-in of one bit reaches one input.
    hi = j("fanin", BITS, "bits.y[7]", "--depth", "0")
    assert hi["data"]["granularity"] == "bit"
    assert "b" in _leaves(hi) and "a" not in _leaves(hi)
    lo = j("fanin", BITS, "bits.y[0]", "--depth", "0")
    assert "a" in _leaves(lo) and "b" not in _leaves(lo)


def test_fanin_bit_window_maps_through_chain():
    # y[7] <- mid[7] <- b[3], carried exactly across the whole-copy and slice.
    d = j("fanin", BITS, "bits.y[7]", "--depth", "0")["data"]
    reached = {(e["source"].split(".")[-1], tuple(e["source_window"] or ()))
               for e in d["edges"]}
    assert ("b", (3, 3)) in reached
    assert all(not e["widened"] for e in d["edges"])


def test_fanin_whole_net_is_net_level():
    d = j("fanin", BITS, "bits.y", "--depth", "0")
    assert d["data"]["granularity"] == "net"
    assert {"a", "b"} <= _leaves(d)


def test_fanin_widens_on_arithmetic():
    # w = a + b has no bit correspondence, so w[3] widens to whole a and b.
    d = j("fanin", BITS, "bits.w[3]", "--depth", "0")["data"]
    assert {"a", "b"} <= {n["path"].split(".")[-1] for n in d["nodes"]}
    assert all(e["widened"] and e["source_window"] is None for e in d["edges"])


def test_fanout_bit_select():
    d = j("fanout", BITS, "bits.a[0]", "--depth", "0")["data"]
    edges = {(e["target"].split(".")[-1], tuple(e["target_window"] or ()), e["widened"])
             for e in d["edges"]}
    assert ("mid", (0, 0), False) in edges     # precise forward map
    assert ("w", (), True) in edges            # arithmetic widens


def test_offset_syntax_matches_declared():
    # For a [7:0] vector the declared index equals the flattened offset.
    a = j("fanin", BITS, "bits.y[7]", "--depth", "0")
    b = j("fanin", BITS, "bits.y@[7]", "--depth", "0")
    assert _leaves(a) == _leaves(b) == {"b", "mid", "y"}


def test_path_carries_bits():
    d = j("path", BITS, "bits.a[0]", "bits.y")["data"]
    assert d["found"] and d["granularity"] == "bit"
    assert d["edges"][-1]["target_window"] == [0, 0]


def test_path_bit_to_bit_exact():
    d = j("path", BITS, "bits.a[0]", "bits.y[0]")["data"]
    assert d["found"] is True
    assert d["from_window"] == [0, 0]
    assert d["to_window"] == [0, 0]
    assert d["granularity"] == "bit"


def test_path_bit_to_wrong_bit_not_found():
    d = j("path", BITS, "bits.a[0]", "bits.y[7]")["data"]
    assert d["found"] is False
    assert d.get("reason") is None


def test_path_net_to_bit():
    d = j("path", BITS, "bits.a", "bits.y[0]")["data"]
    assert d["found"] is True
    assert d["from_window"] is None
    assert d["to_window"] == [0, 0]
    assert d["edges"][0]["source_window"] == [0, 0]


def test_path_bit_precision_lost_on_arithmetic():
    d = j("path", BITS, "bits.a[3]", "bits.w[3]")["data"]
    assert d["found"] is False
    assert d["reason"] == "bit_precision_lost"


def test_path_net_to_arithmetic_bit_succeeds():
    d = j("path", BITS, "bits.a", "bits.w[3]")["data"]
    assert d["found"] is True
    assert d["from_window"] is None
    assert d["to_window"] == [3, 3]


def test_path_same_net_bit_zero_length():
    same = j("path", BITS, "bits.a[3]", "bits.a[3]")["data"]
    assert same["found"] is True and same["length"] == 0
    diff = j("path", BITS, "bits.a[3]", "bits.a[7]")["data"]
    assert diff["found"] is False and diff["length"] == 0


def test_path_reverse_keeps_traversed_slice_edge():
    d = j("path", SAMEPAIR, "samepair.a[7]", "samepair.y[7]")["data"]
    assert d["found"] is True
    assert len(d["edges"]) == 1
    e = d["edges"][0]
    assert e["source_window"] == [7, 7]
    assert e["target_window"] == [7, 7]
    assert e["line"] == 10
    assert e["kind"] == "data"


def test_path_uses_the_edge_bfs_actually_walked():
    # y[3:0]=a[3:0] and y[7:4]=a[7:4] share the same net pair.  The backtrace
    # must use the slice edge the walk followed for a[7], not guess with a
    # LIMIT 1 lookup that could return the low-nibble assignment.
    d = j("path", SAMEPAIR, "samepair.a[7]", "samepair.y")["data"]
    assert d["found"]
    assert len(d["edges"]) == 1
    e = d["edges"][0]
    assert e["source_window"] == [7, 7]
    assert e["target_window"] == [7, 7]
    assert e["line"] == 10
    assert e["statement"] is None or "a[7:4]" in e["statement"]


def test_path_comb_state_as_source_consistent():
    """A state element as source must be reachable in reverse --comb too,
    mirroring forward where the walk simply starts at the flop output."""
    from rtltracer.cone import Facts, _path_bfs
    con = sqlite3.connect(BITS)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    facts = Facts(clocked={1}, latch=set(), dead=set(),
                  call_parent={}, body_local=set(), stmt_branch={})
    # bits.a (net 1) treated as a flop output driving mid -> y combinationally.
    fwd, _ = _path_bfs(cur, facts, 1, 3, None, None, "forward", comb=True)
    rev, _ = _path_bfs(cur, facts, 3, 1, (0, 0), None, "reverse", comb=True)
    assert fwd is not None
    assert rev is not None


def test_path_range_to_range_narrows_to_overlap():
    d = j("path", BITS, "bits.a[0]", "bits.y[3:0]")["data"]
    assert d["found"] is True
    # Reverse-derived window at the source is a[3:0]; the user only asked for
    # a[0], so every reported window must be narrowed to the overlap.
    assert d["edges"][0]["source_window"] == [0, 0]
    assert d["edges"][-1]["target_window"] == [0, 0]
    assert all(e["source_window"] == [0, 0] for e in d["edges"])
    assert all(e["target_window"] == [0, 0] for e in d["edges"])


# --- path -------------------------------------------------------------------

def test_path_found():
    d = j("path", DB, "top.a", "top.q")["data"]
    assert d["found"] is True and d["length"] == 4


def test_path_same_net():
    d = j("path", DB, "top.a", "top.a")["data"]
    assert d["found"] is True and d["length"] == 0
    assert d["nodes"] == ["top.a"]


def test_path_not_found():
    assert j("path", DB, "top.q", "top.a")["data"]["found"] is False


def test_path_comb_blocks_through_flop():
    assert j("path", DB, "top.a", "top.q", "--comb")["data"]["found"] is False


def test_path_option_smoke():
    for extra in (["--no-ctl"], ["--follow-ctl"], ["--ctl-depth", "1"],
                  ["--comb", "--through-latch"], ["--depth", "10"]):
        code, _, _ = run("path", DB, "top.a", "top.q", *extra)
        assert code == 0


def test_path_render_tolerates_null_kind():
    # A trail edge with no matching driver row has kind=None; the human
    # renderer must not crash concatenating it into the connector.
    from rtltracer.cli import _human
    data = {"from": "a", "to": "b", "found": True, "length": 1,
            "nodes": ["a", "b"],
            "edges": [{"source": "a", "target": "b", "kind": None,
                       "file": None, "line": None, "statement": None}]}
    out = _human("path", data, {"found": True, "length": 1, "_ms": 0})
    assert "via ?" in out


# --- envelope / exit codes --------------------------------------------------

def test_json_envelope_shape():
    d = j("info", DB)
    assert {"tool", "version", "status", "command", "data", "summary"} <= set(d)


def test_unknown_signal_exits_one():
    code, _, err = run("trace", DB, "top.does_not_exist")
    assert code == 1 and "error" in err.lower()


# --- regression: path --comb direction consistency ---

def test_path_comb_target_bit_matches_forward():
    whole = j("path", DB, "top.a", "top.q", "--comb")["data"]
    bit = j("path", DB, "top.a", "top.q[0]", "--comb")["data"]
    assert whole["found"] is False
    assert bit["found"] is False


def test_path_reverse_widened_flag():
    d = j("path", BITS, "bits.a", "bits.w[3]")["data"]
    assert d["found"] is True
    assert d["edges"][0]["widened"] is True


# --- regression: source-state candidate fallback + stale not quoted ---

def test_source_state_falls_back_across_candidates():
    import hashlib
    from rtltracer.source import source_state
    with tempfile.TemporaryDirectory() as tmp:
        stale = Path(tmp) / "stale.sv"
        good = Path(tmp) / "good.sv"
        stale.write_text("old content", encoding="utf-8")
        good.write_text("new content", encoding="utf-8")
        digest = hashlib.sha256(good.read_bytes()).hexdigest()
        db = Path(tmp) / "src.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE file(id INTEGER PRIMARY KEY, path TEXT, src_file_id INTEGER)")
        con.execute("CREATE TABLE src_file(id INTEGER PRIMARY KEY, path TEXT, digest TEXT)")
        con.execute("INSERT INTO src_file VALUES (1, ?, ?)", (str(stale), digest))
        con.execute("INSERT INTO file VALUES (1, ?, 1)", (str(good),))
        con.commit()
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        assert source_state(cur, str(good))[2] == "current"
        con.close()


def test_source_stale_not_quoted():
    import hashlib
    from rtltracer.source import Source, source_state
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.sv"
        src.write_text("line1\nline2\n", encoding="utf-8")
        wrong_digest = "0" * 64
        db = Path(tmp) / "src.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE file(id INTEGER PRIMARY KEY, path TEXT, src_file_id INTEGER)")
        con.execute("CREATE TABLE src_file(id INTEGER PRIMARY KEY, path TEXT, digest TEXT)")
        con.execute("INSERT INTO src_file VALUES (1, ?, ?)", (str(src), wrong_digest))
        con.execute("INSERT INTO file VALUES (1, ?, 1)", (str(src),))
        con.commit()
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        assert source_state(cur, str(src))[2] == "stale"
        assert Source(cur).line(str(src), 1) == (None, "stale")
        con.close()


def test_trace_far_windows_merge_intervals():
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "merge.db"
        shutil.copyfile(SAMEPAIR, db)
        con = sqlite3.connect(db)
        con.execute("UPDATE net_dep SET stmt_id=1, src_lo=6, src_hi=7, tgt_lo=6, tgt_hi=7 WHERE id=2")
        con.commit()
        con.close()
        d = j("trace", str(db), "samepair.y@[0:7]")["data"]
        assert len(d["hops"]) == 1
        fw = d["hops"][0]["far_windows"]
        assert fw.get("samepair.a") == [[0, 3], [6, 7]]


def test_offset_select_out_of_range():
    code, _, _ = run("trace", BITS, "bits.y@[99]")
    assert code == 1
    assert j("trace", BITS, "bits.y@[99]")["errors"][0]["code"] == "BAD_SELECT"


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
