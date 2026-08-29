"""fanin, fanout, path — the cone commands. BFS engine only, three
pruning rules: state elements (--comb, --through-latch), dead branches
(constant-condition arms), and call-site isolation (admissible /
next_ctx).  Gating: --no-ctl excludes control arcs; --follow-ctl /
--ctl-depth=N follow them instead of stopping at them."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from rtltracer import sql
from rtltracer.db import Db
from rtltracer.resolve import resolve


@dataclass
class Facts:
    clocked: set[int] = None
    latch: set[int] = None
    dead: set[int] = None
    call_parent: dict = None
    body_local: set[int] = None
    stmt_branch: dict = None


def load_facts(cursor) -> Facts:
    f = Facts()
    state_rows = cursor.execute(sql.load("state_elements")).fetchall()
    f.clocked = {r["net_id"] for r in state_rows if r["kind"] == "clocked"}
    f.latch = {r["net_id"] for r in state_rows if r["kind"] == "latch"}
    f.dead = {r["branch_id"] for r in cursor.execute(sql.load("dead_branches"))}
    f.call_parent = {r["call_site_id"]: r["parent_call_site_id"]
                     for r in cursor.execute(sql.load("call_parents"))}
    f.body_local = {r["net_id"] for r in cursor.execute(sql.load("body_local"))}
    f.stmt_branch = {}
    return f


def _arcs_batch(cursor, name: str, nets: list[int], no_ctl: int) -> list[dict]:
    text, values = sql.fill(name, nets)
    return [dict(r) for r in cursor.execute(text, [*values, no_ctl])]


def _unreachable(cursor, facts: Facts, stmt_id) -> bool:
    if not facts.dead or stmt_id is None:
        return False
    if stmt_id not in facts.stmt_branch:
        row = cursor.execute(sql.load("stmt_branch"), {"stmt_id": stmt_id}).fetchone()
        facts.stmt_branch[stmt_id] = row["branch_id"] if row else None
    return facts.stmt_branch[stmt_id] in facts.dead


def _admissible(facts: Facts, row_site: int | None, ctx: int | None) -> bool:
    if row_site is None or ctx is None:
        return True
    return row_site == ctx or facts.call_parent.get(row_site) == ctx


def _next_ctx(facts: Facts, row: dict, ctx: int | None, far: int) -> int | None:
    site = row.get("call_site_id")
    if site is None:
        return ctx if far in facts.body_local else None
    if row.get("dep_kind") == "procedure":
        leaving = ctx == site
        outer = facts.call_parent.get(site)
        nxt = outer if leaving else site
        return nxt if far in facts.body_local else None
    return site if far in facts.body_local else None


def _at_state(facts: Facts, comb: bool, through_latch: bool, far: int) -> bool:
    if not comb:
        return False
    return far in facts.clocked or (far in facts.latch and not through_latch)


def _cone_bfs(cursor, facts: Facts, name: str, start: int, no_ctl: bool,
              comb: bool, through_latch: bool, follow_ctl: bool,
              ctl_depth: int | None, depth: int, direction: str) -> list[dict]:
    """BFS with call-site isolation, state-element and dead-code pruning,
    and configurable gating.  visited = (net, ctx, ctl_left)."""
    ctl_init = 0 if not follow_ctl and ctl_depth is None else (ctl_depth if ctl_depth is not None else -1)
    visited = {(start, None, ctl_init)}
    frontier = [(start, None, ctl_init)]
    edges = []
    no = int(no_ctl)
    d = 0
    while frontier and (depth == 0 or d < depth):  # depth 0 walks to closure
        d += 1
        nets = [net for net, _, _ in frontier]
        rows = _arcs_batch(cursor, name, nets, no)
        by_signal: dict[int, list[dict]] = {}
        for r in rows:
            key = r["tgt_net_id"] if direction == "driver" else r["src_net_id"]
            by_signal.setdefault(key, []).append(r)

        next_frontier = []
        for net, ctx, ctl_left in frontier:
            for r in by_signal.get(net, []):
                if not _admissible(facts, r.get("call_site_id"), ctx):
                    continue
                is_control = r.get("dep_kind") == "control"
                # Gating: no_ctl already filtered by SQL, but for Direct
                # (ctl_left == 0, no follow_ctl, no ctl_depth) we stop here.
                if is_control and ctl_left == 0 and ctl_depth is None and not follow_ctl:
                    r["_depth"] = d
                    r["ends_at_state"] = False
                    r["_unreachable"] = _unreachable(cursor, facts, r.get("stmt_id"))
                    edges.append(r)
                    continue
                far = r["tgt_net_id"]
                r["_depth"] = d
                r["ends_at_state"] = _at_state(facts, comb, through_latch, far)
                r["_unreachable"] = _unreachable(cursor, facts, r.get("stmt_id"))
                edges.append(r)
                if r["_unreachable"]:
                    continue
                if r["ends_at_state"]:
                    continue
                nctx = _next_ctx(facts, r, ctx, far)
                nctl = ctl_left
                if is_control and ctl_left >= 0:
                    if ctl_left == 0:
                        continue
                    nctl = ctl_left - 1
                key = (far, nctx, nctl)
                if key not in visited:
                    visited.add(key)
                    next_frontier.append((far, nctx, nctl))
        frontier = next_frontier
    return edges


def _walk(cursor, db: Db, signal: str, direction: str, depth: int,
          no_ctl: bool, comb: bool, through_latch: bool,
          follow_ctl: bool, ctl_depth: int | None, top: str) -> dict:
    sig = resolve(cursor, signal, top)
    facts = load_facts(cursor)
    bfs_name = "fanin_bfs" if direction == "driver" else "fanout_bfs"
    raw = _cone_bfs(cursor, facts, bfs_name, sig.net_id, no_ctl, comb,
                    through_latch, follow_ctl, ctl_depth, depth, direction)
    if depth > 0:
        raw = [r for r in raw if r["_depth"] <= depth]
    if no_ctl:
        raw = [r for r in raw if r.get("dep_kind") != "control"]
    edges, nodes = _name_edges(cursor, raw, direction)
    direct = sorted({e["source"] for e in edges if e["depth"] == 1})
    data = {
        "start": sig.full_path,
        "direction": direction,
        "max_depth": None if depth == 0 else depth,
        "comb": comb,
        "through_latch": through_latch,
        "no_ctl": no_ctl,
        "follow_ctl": follow_ctl,
        "ctl_depth": ctl_depth,
        "nodes": nodes,
        "edges": edges,
        "direct": direct,
    }
    summary = {
        "nodes": len(nodes),
        "edges": len(edges),
        "direct": len(direct),
        "stopped_at_state": sum(1 for e in edges if e["ends_at_state"]),
        "max_depth_reached": max((e["depth"] for e in edges), default=0),
        "control_edges": sum(1 for e in edges if e["control"]),
        "limit": 0,
    }
    return {"data": data, "summary": summary, "diagnostics": []}


def _name_edges(cursor, raw: list[dict], direction: str):
    net_ids = set()
    for r in raw:
        net_ids.add(r["src_net_id"])
        net_ids.add(r["tgt_net_id"])
    names = {}
    if net_ids:
        names = _name_nets(cursor, net_ids)
    edges = []
    for r in raw:
        src, tgt = r["src_net_id"], r["tgt_net_id"]
        kind = r.get("driver_kind") or r.get("load_kind")
        edges.append({
            "source": names.get(src, f"<net {src}>"),
            "target": names.get(tgt, f"<net {tgt}>"),
            "kind": kind,
            "raw_kind": r.get("dep_kind"),
            "depth": r["_depth"],
            "boundary": kind in ("connection", "connection_expression") or r.get("driver_ref") is not None or r.get("load_ref") is not None,
            "control": r.get("dep_kind") == "control",
            "ends_at_state": r.get("ends_at_state", False),
            "unreachable": r.get("_unreachable", False),
            "file": r.get("file_path"),
            "line": r.get("src_line"),
        })
    depth_map = {}
    for e in edges:
        if direction == "driver":
            depth_map.setdefault(e["source"], e["depth"])
            depth_map.setdefault(e["target"], e["depth"] - 1)
        else:
            depth_map.setdefault(e["source"], e["depth"] - 1)
            depth_map.setdefault(e["target"], e["depth"])
    nodes = [{"path": n, "depth": d}
             for n, d in sorted(depth_map.items(), key=lambda kv: (kv[1], kv[0]))]
    return edges, nodes


def _name_nets(cursor, net_ids) -> dict[int, str]:
    text, values = sql.fill("net_names", sorted(net_ids))
    return {r["net_id"]: r["full_path"] for r in cursor.execute(text, values)}


def _path_bfs(cursor, facts: Facts, from_id: int, to_id: int, max_depth: int,
              no_ctl: bool, comb: bool, through_latch: bool,
              follow_ctl: bool, ctl_depth: int | None):
    """Shortest path via BFS, same pruning as the cone walk."""
    if from_id == to_id:
        return [from_id]
    ctl_init = 0 if not follow_ctl and ctl_depth is None else (ctl_depth if ctl_depth is not None else -1)
    visited = {(from_id, None, ctl_init)}
    parents: dict[tuple[int, int | None, int], tuple[int, int | None, int]] = {}
    frontier = [(from_id, None, ctl_init)]
    no = int(no_ctl)
    steps = 0
    while frontier and (max_depth == 0 or steps < max_depth):
        nets = [n for n, _, _ in frontier]
        rows = _arcs_batch(cursor, "fanout_bfs", nets, no)
        by_signal: dict[int, list[dict]] = {}
        for r in rows:
            by_signal.setdefault(r["src_net_id"], []).append(r)
        next_frontier = []
        for net, ctx, ctl_left in frontier:
            for r in by_signal.get(net, []):
                if not _admissible(facts, r.get("call_site_id"), ctx):
                    continue
                is_control = r.get("dep_kind") == "control"
                if is_control and ctl_left == 0 and ctl_depth is None and not follow_ctl:
                    continue
                far = r["tgt_net_id"]
                if _unreachable(cursor, facts, r.get("stmt_id")):
                    continue
                if _at_state(facts, comb, through_latch, far):
                    continue
                nctx = _next_ctx(facts, r, ctx, far)
                nctl = ctl_left
                if is_control and ctl_left >= 0:
                    if ctl_left == 0:
                        continue
                    nctl = ctl_left - 1
                key = (far, nctx, nctl)
                if key in visited:
                    continue
                visited.add(key)
                parents[key] = (net, ctx, ctl_left)
                if far == to_id:
                    trail = []
                    at = key
                    while at is not None:
                        trail.append(at[0])
                        at = parents.get(at)
                    return trail[::-1]
                next_frontier.append((far, nctx, nctl))
        frontier = next_frontier
        steps += 1
    return None


def path(db: Db, from_sig: str, to_sig: str, max_depth: int = 0,
         no_ctl: bool = False, comb: bool = False, through_latch: bool = False,
         follow_ctl: bool = False, ctl_depth: int | None = None, top: str = "") -> dict:
    cur = db.conn.cursor()
    f = resolve(cur, from_sig, top)
    t = resolve(cur, to_sig, top)
    facts = load_facts(cur)
    trail = _path_bfs(cur, facts, f.net_id, t.net_id, max_depth,
                      no_ctl, comb, through_latch, follow_ctl, ctl_depth)
    found = trail is not None
    edges = []
    nodes = []
    if found:
        names = _name_nets(cur, trail)
        nodes = [names.get(n, f"<net {n}>") for n in trail]
        for a, b in zip(trail, trail[1:]):
            r = cur.execute(sql.load("path_edge"),
                            {"signal_net_id": b, "driver_net_id": a}).fetchone()
            edges.append({
                "source": names.get(a, f"<net {a}>"),
                "target": names.get(b, f"<net {b}>"),
                "kind": r["driver_kind"] if r else None,
                "file": r["file_path"] if r else None,
                "line": r["src_line"] if r else None,
            })
    data = {
        "from": f.full_path,
        "to": t.full_path,
        "found": found,
        "length": len(trail) - 1 if found else 0,
        "nodes": nodes,
        "edges": edges,
    }
    summary = {"found": found, "length": len(trail) - 1 if found else 0, "clocked_edges": 0}
    return {"data": data, "summary": summary, "diagnostics": []}
