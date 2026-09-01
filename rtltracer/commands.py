"""info, tree, find, trace — the non-cone commands. Each returns a dict that
the renderer turns into terminal text or JSON; SQL stays in rtltracer/sql/."""
from __future__ import annotations

from rtltracer.sql import sql
from rtltracer.bits import SKIP, merge_intervals, propagate
from rtltracer.db import Db, net_names
from rtltracer.resolve import ResolveError, resolve
from rtltracer.source import Source, source_diagnostics, source_state


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def info(db: Db) -> dict:
    cur = db.conn.cursor()
    seal = cur.execute(sql.load("info")).fetchone()
    sources = cur.execute(sql.load("info_sources")).fetchall()
    unresolved = cur.execute(sql.load("info_unresolved")).fetchone()
    rows = []
    stale = missing = 0
    for s in sources:
        src, digest, state = source_state(cur, s["file_path"])
        rows.append({"path": s["src_path"], "state": state})
        stale += state == "stale"
        missing += state == "missing"
    data = {
        "path": db.path,
        "schema_version": seal["schema_version"],
        "producer": {
            "tool": seal["tool"],
            "tool_version": seal["tool_version"],
            "slang_version": seal["slang_version"],
            "revision": seal["producer_revision"],
            "config_digest": seal["config_digest"],
        },
        "top": seal["top"],
        "analysis": {
            "status": seal["analysis_status"],
            "error_count": seal["error_count"],
            "unresolved_count": seal["unresolved_count"],
            "empty_procedure_count": seal["empty_procedure_count"],
            "duplicate_path_count": seal["duplicate_path_count"],
            "recursion_count": seal["recursion_count"],
            "truncated_call_count": seal["truncated_call_count"],
            "checker_inst_count": seal["checker_inst_count"],
            "unanalysed_inst_count": seal["unanalysed_inst_count"],
            "unresolved_ref_reads": unresolved["unresolved_reads"],
            "unresolved_ref_writes": unresolved["unresolved_writes"],
        },
        "sources": rows,
    }
    summary = {
        "schema_version": seal["schema_version"],
        "analysis_status": seal["analysis_status"],
        "sources": len(rows),
        "sources_stale": stale,
        "sources_missing": missing,
    }
    return {"data": data, "summary": summary, "diagnostics": []}


def tree(db: Db, scope: str | None, depth: int = 3, limit: int = 0) -> dict:
    cur = db.conn.cursor()
    if scope is None:
        root = cur.execute(sql.load("tops")).fetchone()
        if root is None:
            raise ResolveError("NO_TOP", "this database elaborated no top")
        node_id, path = root["node_id"], root["node_name"]
    else:
        try:
            # A net names a scope: the instance that declares it.
            sig = resolve(cur, scope)
            node_id, path = sig.inst_id, sig.node_path
        except ResolveError:
            # Not a net: try the tree level itself.
            row = cur.execute(sql.load("node_by_path"), {"node_path": scope}).fetchone()
            if row is None:
                raise ResolveError("SCOPE_NOT_FOUND", f"'{scope}' does not name a scope")
            node_id, path = row["node_id"], row["node_path"]
    walk_depth = depth if depth > 0 else 1_000_000
    rows = cur.execute(sql.load("tree"), {"root_node_id": node_id, "max_depth": walk_depth}).fetchall()
    levels = []
    for r in rows:
        levels.append({
            "path": r["path"],
            "kind": r["node_kind"],
            "module": r["module_name"] or r["def_name"],
            "depth": r["depth"],
            "nets": r["nets"],
            "children": r["children"],
        })
    shown = levels if limit == 0 else levels[:limit]
    data = {"root": path, "max_depth": None if depth == 0 else depth, "levels": shown}
    summary = {
        "levels": len(levels),
        "shown": len(shown),
        "truncated": len(levels) > len(shown),
        "depth_truncated": depth > 0 and any(l["depth"] >= depth and l["children"] > 0 for l in levels),
        "limit": limit,
    }
    return {"data": data, "summary": summary, "diagnostics": []}


def find(db: Db, pattern: str, kind: str = "net", limit: int = 200) -> dict:
    cur = db.conn.cursor()
    root = cur.execute(sql.load("tops")).fetchone()
    root_path = root["node_name"] if root else ""
    # Fetch one past the limit to tell truncation from an exact fill; limit 0
    # means all, which SQLite's LIMIT reads as -1.
    probe = limit + 1 if limit > 0 else -1
    if kind == "net":
        rows = cur.execute(sql.load("find_net"),
                           {"pattern": pattern, "root_path": root_path, "limit": probe}).fetchall()
        hits = [{"path": r["full_path"], "what": "net",
                 "detail": f"{r['decl_kind']}, {_plural(r['width'], 'bit')}"
                           if r["width"] is not None else r["decl_kind"]}
                for r in rows]
    elif kind == "instance":
        rows = cur.execute(sql.load("find_instance"),
                           {"pattern": pattern, "root_path": root_path, "limit": probe}).fetchall()
        hits = [{"path": r["node_path"], "what": r["node_kind"], "detail": r["module_name"] or r["def_name"]}
                for r in rows]
    else:  # module
        rows = cur.execute(sql.load("find_module"), {"pattern": pattern, "limit": probe}).fetchall()
        hits = [{"path": r["name"], "what": r["def_kind"],
                 "detail": _plural(r["occurrences"], "instance")}
                for r in rows]
    truncated = limit > 0 and len(hits) > limit
    if truncated:
        hits = hits[:limit]
    data = {"pattern": pattern, "kind": kind, "hits": hits}
    summary = {"hits": len(hits), "shown": len(hits), "truncated": truncated, "limit": limit}
    return {"data": data, "summary": summary, "diagnostics": []}


def _provenance(row: dict, index: int, dep_kind: str | None) -> str:
    role = ":control" if dep_kind == "control" else ""
    if row.get("stmt_id") is not None:
        return f"s{row['stmt_id']}{role}"
    if row.get("conn_id") is not None:
        return f"c{row['conn_id']}"
    if row.get("prim_id") is not None:
        return f"p{row['prim_id']}"
    if row.get("term_id") is not None:
        return f"t{row['term_id']}"
    if row.get("proc_id") is not None:
        return f"proc{row['proc_id']}"
    return f"row{index}"


def _gates(cur, stmt_id: int | None, source: Source) -> tuple[list[dict], bool]:
    if stmt_id is None:
        return [], False
    rows = cur.execute(sql.load("trace_gates"), {"stmt_id": stmt_id}).fetchall()
    gates = []
    unreachable = False
    for r in rows:
        if r["static_taken"] == 0:
            unreachable = True
        iteration = None
        if r["iter_name"] and r["iter_first"] is not None and r["iter_step"] is not None and r["iter_count"] is not None:
            iteration = f"{r['iter_name']} = {r['iter_first']}, step {r['iter_step']}, {r['iter_count']} iteration(s)"
        elif r["iter_name"]:
            iteration = r["iter_name"]
        gates.append({
            "kind": r["branch_kind"],
            "sense": r["sense"],
            "case_kind": r["case_kind"],
            "check": r["check_kind"],
            "ordinal": r["ordinal"],
            "labels": r["labels"],
            "reads": r["reads"].split(",") if r["reads"] else [],
            "static_taken": r["static_taken"],
            "iteration": iteration,
            "line": r["src_line"],
        })
    return gates, unreachable


def _timing(cur, stmt_row) -> dict | None:
    if stmt_row is None or stmt_row["proc_id"] is None:
        return None
    proc_id = stmt_row["proc_id"]
    kind = cur.execute(sql.load("trace_prockind"), {"proc_id": proc_id}).fetchone()
    if kind is None:
        return None
    events = cur.execute(sql.load("trace_events"), {"proc_id": proc_id}).fetchall()
    return {
        "proc_kind": kind["proc_kind"],
        "events": [{"edge": e["edge_kind"], "signal": e["net_name"] or "<expression>"} for e in events],
    }


def _call_chain(cur, call_site_id: int | None) -> list[str]:
    if call_site_id is None:
        return []
    chain = []
    at = call_site_id
    while at is not None:
        row = cur.execute(sql.load("trace_calls"), {"call_site_id": at}).fetchone()
        if row is None:
            break
        chain.append(f"{row['subroutine_name']}()")
        at = row["parent_call_site_id"]
    chain.reverse()
    return chain


def _attach_far(hop: dict, far_net, far_ref, bit_mode: bool, far_widened: bool, far_window):
    """Record one arc's far end on its hop: the driven/read net (carrying its
    bit window, or marked widened, in bit mode), else an unresolved reference."""
    if far_net is not None:
        hop["signals"].add(far_net)
        if bit_mode:
            if far_widened:
                hop["_widened_far"].add(far_net)
            elif far_window is not None:
                hop["_far_windows"].setdefault(far_net, []).append(far_window)
    elif far_ref:
        hop["unresolved"].add(far_ref)


def trace(db: Db, signal: str, load: bool = False, ctl: bool = False, top: str = "") -> dict:
    cur = db.conn.cursor()
    sig = resolve(cur, signal, top)
    name = "trace_load" if load else "trace_driver"
    rows = cur.execute(sql.load(name), {"net_id": sig.net_id, "ctl": int(ctl)}).fetchall()
    source = Source(cur)
    hops = []
    procedures: list[dict] = []
    proc_index: dict[int, int] = {}
    far_ids = set()
    stmt_cache: dict[int, dict] = {}
    raw = [dict(r) for r in rows]

    # Serve every hop's scope from one whole-tree read (see node_paths.sql),
    # never a lookup per hop.
    node_paths: dict[int, str] = {}
    def scope_path(node_id: int) -> str | None:
        if not node_paths:
            node_paths.update((r["node_id"], r["node_path"])
                              for r in cur.execute(sql.load("node_paths")))
        return node_paths.get(node_id)

    # Bit mode: carry the requested window across each arc. SKIP rows feed
    # other bits (filtered out); None widens the far end to the whole net.
    # Control rows are gated the same way: they carry no bit correspondence.
    bit_mode = sig.window is not None
    arc_result: dict[int, object] = {}
    if bit_mode:
        for i, row in enumerate(raw):
            if row["dep_kind"] == "control":
                arc_result[i] = None
                continue
            arc_result[i] = propagate(sig.window,
                                      row["near_lo"], row["near_hi"],
                                      row["far_lo"], row["far_hi"],
                                      row["map_kind"])
    for index, row in enumerate(raw):
        # Bit mode: an arc that does not touch the requested window feeds
        # other bits and is filtered out of the answer entirely.
        if bit_mode and arc_result[index] is SKIP:
            continue
        kind = row["kind"]
        dk = row["dep_kind"]
        key = _provenance(row, index, dk)
        existing = next((h for h in hops if h["_key"] == key), None)
        far_net = row["far_net_id"]
        far_ref = row["far_ref"]
        if far_net is not None:
            far_ids.add(far_net)
        if bit_mode:
            res = arc_result[index]
            far_window = None if res is None else res     # None = widened whole
            # A condition gates the whole statement; it is not a value
            # mapping, so it is never "precision widened".
            far_widened = res is None and dk != "control"
        else:
            far_window, far_widened = None, False

        if existing is not None:
            _attach_far(existing, far_net, far_ref, bit_mode, far_widened, far_window)
            continue

        stmt_dict = None
        if row.get("stmt_id") is not None:
            if row["stmt_id"] not in stmt_cache:
                matches = cur.execute(sql.fill("trace_stmt", [row["stmt_id"]])[0],
                                      [row["stmt_id"]]).fetchall()
                stmt_cache.update({r["stmt_id"]: dict(r) for r in matches})
            stmt_dict = stmt_cache.get(row["stmt_id"])
        file_path = stmt_dict["file_path"] if stmt_dict and stmt_dict["file_path"] else row.get("file_path")
        line = stmt_dict["src_line"] if stmt_dict and stmt_dict["src_line"] else row.get("src_line")
        statement, source_state = source.line(file_path, line) if file_path else (None, "missing")
        scope = sig.full_path
        if stmt_dict:
            scope = scope_path(stmt_dict["scope_node_id"]) or sig.full_path
        gates, unreachable = _gates(cur, stmt_dict["stmt_id"] if stmt_dict else None, source)
        timing = _timing(cur, stmt_dict)
        call_chain = _call_chain(cur, stmt_dict["call_site_id"] if stmt_dict else None)
        raw_kind = (stmt_dict or {}).get("construct") or (stmt_dict or {}).get("stmt_kind") or kind
        procedure = None
        if stmt_dict and stmt_dict["proc_id"] is not None:
            pid = stmt_dict["proc_id"]
            if pid not in proc_index:
                writes = cur.execute(sql.load("trace_procwrites"),
                                     {"proc_id": pid, "net_id": sig.net_id}).fetchall()
                if len(writes) >= 2:
                    pk = cur.execute(sql.load("trace_prockind"), {"proc_id": pid}).fetchone()
                    proc_writes = []
                    for w in writes:
                        ws, ws_state = source.line(file_path, w["src_line"]) if file_path else (None, "missing")
                        proc_writes.append({
                            "sequence": w["sequence"],
                            "line": w["src_line"],
                            "statement": ws,
                            "bits": None,
                            "unconditional": w["branch_id"] is None,
                            "call_chain": _call_chain(cur, w["call_site_id"]),
                        })
                    proc_index[pid] = len(procedures)
                    procedures.append({"proc_kind": pk["proc_kind"] if pk else None, "writes": proc_writes})
                else:
                    proc_index[pid] = None
            procedure = proc_index.get(pid)
        hop = {
            "_key": key,
            "kind": kind,
            "raw_kind": raw_kind,
            "statement": statement,
            "source": source_state,
            "scope": scope,
            "file": file_path,
            "line": line,
            "boundary": kind in ("connection", "connection_expression") or far_ref is not None,
            "signals": set(),
            "unresolved": set(),
            "assign_kind": (stmt_dict or {}).get("assign_kind"),
            "sequence": (stmt_dict or {}).get("sequence"),
            "timing": timing,
            "gates": gates,
            "procedure": procedure,
            "unreachable": unreachable,
            "call_chain": call_chain,
            "_dep_kind": dk,
        }
        if bit_mode:
            hop["_far_windows"] = {}
            hop["_widened_far"] = set()
        _attach_far(hop, far_net, far_ref, bit_mode, far_widened, far_window)
        hops.append(hop)

    names = net_names(cur, far_ids)
    for h in hops:
        h["signals"] = sorted(names.get(n, f"<net {n}>") for n in h["signals"])
        h["unresolved"] = sorted(h["unresolved"])
        if bit_mode:
            h["far_windows"] = {}
            for n, w in h["_far_windows"].items():
                name = names.get(n, f"<net {n}>")
                h["far_windows"][name] = (
                    None if n in h["_widened_far"] else
                    [[lo, hi] for lo, hi in merge_intervals(w)])
            for n in h["_widened_far"]:
                h["far_windows"].setdefault(names.get(n, f"<net {n}>"), None)
            h["widened_far"] = sorted(
                names.get(n, f"<net {n}>") for n in h["_widened_far"])
            h.pop("_far_windows", None)
            h.pop("_widened_far", None)
        h.pop("_key", None)
        h["dep_kind"] = h.pop("_dep_kind", None)

    structural = [h for h in hops if h["kind"] not in ("alias", "control")]
    if not structural:
        status = "no_load_found" if load else "no_driver_found"
    elif all(h["kind"] in ("connection", "terminal") for h in structural):
        status = "boundary_only"
    else:
        status = "resolved"
    data = {
        "signal": sig.full_path,
        "direction": "load" if load else "driver",
        "granularity": "bit" if sig.window else "net",
        "start_window": list(sig.window) if sig.window else None,
        "bits": sig.spell,
        "width": sig.width,
        "status": status,
        "hops": hops,
        "procedures": procedures,
    }
    summary = {"status": status, "hops": len(hops), "structural_hops": len(structural)}
    return {"data": data, "summary": summary, "diagnostics": source_diagnostics(source)}
