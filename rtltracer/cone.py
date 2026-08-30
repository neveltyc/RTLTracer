"""fanin, fanout, path - the cone commands. Graph edges come from v_trace_edge.

BFS engine only, three pruning rules: state elements (--comb, --through-latch),
dead branches (constant-condition arms), and call-site isolation (admissible /
next_ctx). Gating: --no-ctl excludes control edges; --follow-ctl / --ctl-depth=N
follow them instead of stopping at them."""

from __future__ import annotations

from dataclasses import dataclass

from rtltracer.sql import sql
from rtltracer.bits import SKIP, propagate, uncovered
from rtltracer.db import Db, net_names
from rtltracer.resolve import resolve
from rtltracer.source import Source

FOLLOW_ALL = -1


@dataclass
class Facts:
    clocked: set[int] = None
    latch: set[int] = None
    dead: set[int] = None            # stmt_ids under a statically-dead branch
    call_parent: dict = None
    body_local: set[int] = None


def load_facts(cursor) -> Facts:
    f = Facts()
    state_rows = cursor.execute(sql.load("state_elements")).fetchall()
    f.clocked = {r["net_id"] for r in state_rows if r["kind"] == "clocked"}
    f.latch = {r["net_id"] for r in state_rows if r["kind"] == "latch"}
    f.dead = {r["stmt_id"] for r in cursor.execute(sql.load("dead_stmts"))}
    f.call_parent = {r["call_site_id"]: r["parent_call_site_id"]
                     for r in cursor.execute(sql.load("call_parents"))}
    f.body_local = {r["net_id"] for r in cursor.execute(sql.load("body_local"))}
    return f


def _ctl_left(ctl_depth: int | None, follow_ctl: bool) -> int:
    """Normalize the CLI control options into one traversal budget:
    0 means stop at control edges, FOLLOW_ALL means follow them all."""
    if ctl_depth is not None:
        return ctl_depth
    return FOLLOW_ALL if follow_ctl else 0


def _stop_nets(facts: Facts, comb: bool, through_latch: bool) -> set[int]:
    """Nets a combinational walk must stop at, resolved once from the options."""
    if not comb:
        return set()
    stop = set(facts.clocked)
    if not through_latch:
        stop |= set(facts.latch)
    return stop


def _admissible(facts: Facts, row_site: int | None, ctx: int | None) -> bool:
    if row_site is None or ctx is None:
        return True
    return row_site == ctx or facts.call_parent.get(row_site) == ctx


def _next_ctx(facts: Facts, row: dict, ctx: int | None, far: int) -> int | None:
    site = row.get("call_site_id")
    if site is None:
        return ctx if far in facts.body_local else None
    if row.get("edge_kind") == "procedure":
        leaving = ctx == site
        outer = facts.call_parent.get(site)
        nxt = outer if leaving else site
        return nxt if far in facts.body_local else None
    return site if far in facts.body_local else None


def _arcs_batch(cursor, name: str, nets: list[int]) -> list[dict]:
    text, values = sql.fill(name, nets)
    return [dict(r) for r in cursor.execute(text, values)]


def _advance(facts: Facts, row: dict, ctx: int | None, ctl_left: int,
             window, stop_nets: set[int]):
    """Walk one edge row `near -> far`: returns (far_net, far_window,
    next_ctx, next_ctl, widened, unreachable) or None when the edge
    cannot be traversed."""
    if not _admissible(facts, row.get("call_site_id"), ctx):
        return None
    is_control = row.get("edge_kind") == "control"
    if is_control and ctl_left == 0:
        return None
    # A condition gates the whole statement; it is not bit-mappable.
    far_window = None
    if not is_control:
        far_window = propagate(window,
                               row["near_lo"], row["near_hi"],
                               row["far_lo"], row["far_hi"],
                               row["map_kind"])
        if far_window is SKIP:
            return None
    unreachable = row["stmt_id"] in facts.dead
    far_net = row["far_net_id"]
    ends_at_state = far_net in stop_nets
    if unreachable or ends_at_state:
        return (far_net, far_window, None, ctl_left,
                far_window is None and not is_control, unreachable)
    nctx = _next_ctx(facts, row, ctx, far_net)
    nctl = ctl_left - 1 if is_control and ctl_left >= 0 else ctl_left
    return (far_net, far_window, nctx, nctl,
            far_window is None and not is_control, unreachable)


def _cone_bfs(cursor, facts: Facts, name: str, start: int,
              ctl_left: int, stop_nets: set[int],
              depth: int, start_window) -> list[dict]:
    """BFS with call-site isolation, state-element and dead-code pruning.
    State is (net, ctx, ctl_left, window); coverage keeps each net's walked
    bits so the same bits are never re-walked."""
    covered: dict = {}
    uncovered(covered, (start, None, ctl_left), start_window)
    frontier = [(start, None, ctl_left, start_window)]
    edges = []
    d = 0
    while frontier and (depth == 0 or d < depth):  # depth 0 walks to closure
        d += 1
        nets = [net for net, _, _, _ in frontier]
        rows = _arcs_batch(cursor, name, nets)
        by_signal: dict[int, list[dict]] = {}
        for r in rows:
            by_signal.setdefault(r["near_net_id"], []).append(r)

        next_frontier = []
        for net, ctx, ctl_left, window in frontier:
            for r in by_signal.get(net, []):
                step = _advance(facts, r, ctx, ctl_left, window, stop_nets)
                if step is None:
                    continue
                far_net, far_window, nctx, nctl, widened, unreachable = step
                r["_depth"] = d
                r["_cur_window"] = window
                r["_far_window"] = far_window
                r["_widened"] = widened
                r["_unreachable"] = unreachable
                r["ends_at_state"] = far_net in stop_nets
                edges.append(r)
                if unreachable or r["ends_at_state"]:
                    continue
                for part in uncovered(covered, (far_net, nctx, nctl), far_window):
                    next_frontier.append((far_net, nctx, nctl, part))
        frontier = next_frontier
    return edges


def walk(cursor, db: Db, signal: str, direction: str, depth: int,
         no_ctl: bool, comb: bool, through_latch: bool,
         follow_ctl: bool, ctl_depth: int | None, top: str) -> dict:
    sig = resolve(cursor, signal, top)
    facts = load_facts(cursor)
    bfs_name = "fanin_bfs" if direction == "driver" else "fanout_bfs"
    raw = _cone_bfs(cursor, facts, bfs_name, sig.net_id,
                    _ctl_left(ctl_depth, follow_ctl), _stop_nets(facts, comb, through_latch),
                    depth, sig.window)
    edges, nodes = _name_edges(cursor, raw, direction)
    data = {
        "start": sig.full_path,
        "direction": direction,
        "granularity": "bit" if sig.window else "net",
        "start_window": list(sig.window) if sig.window else None,
        "max_depth": None if depth == 0 else depth,
        "comb": comb,
        "through_latch": through_latch,
        "no_ctl": no_ctl,
        "follow_ctl": follow_ctl,
        "ctl_depth": ctl_depth,
        "nodes": nodes,
        "edges": edges,
    }
    summary = {
        "nodes": len(nodes),
        "edges": len(edges),
        "stopped_at_state": sum(1 for e in edges if e["ends_at_state"]),
        "max_depth_reached": max((e["depth"] for e in edges), default=0),
        "control_edges": sum(1 for e in edges if e["control"]),
    }
    return {"data": data, "summary": summary, "diagnostics": []}


def _name_edges(cursor, raw: list[dict], direction: str):
    net_ids = set()
    for r in raw:
        net_ids.add(r["src_net_id"])
        net_ids.add(r["dst_net_id"])
    names = net_names(cursor, net_ids)
    src_reader = Source(cursor)
    edges = []
    for r in raw:
        src, tgt = r["src_net_id"], r["dst_net_id"]
        kind = r.get("edge_kind")
        file_path, line = r.get("file_path"), r.get("src_line")
        statement = src_reader.line(file_path, line)[0] if file_path and line else None
        # The frontier net's window sits on the near end; the far net's on the
        # other side. For a fan-in the frontier is the target, for a fan-out the source.
        cur_win, far_win = r.get("_cur_window"), r.get("_far_window")
        target_win, source_win = (cur_win, far_win) if direction == "driver" else (far_win, cur_win)
        edges.append({
            "source": names.get(src, f"<net {src}>"),
            "target": names.get(tgt, f"<net {tgt}>"),
            "kind": kind,
            "edge_source": r.get("edge_source"),
            "depth": r["_depth"],
            "boundary": r.get("edge_source") == "conn_arc" or kind in ("connection", "connection_expression"),
            "control": kind == "control",
            "ends_at_state": r.get("ends_at_state", False),
            "unreachable": r.get("_unreachable", False),
            "source_window": list(source_win) if source_win else None,
            "target_window": list(target_win) if target_win else None,
            "widened": r.get("_widened", False),
            "file": file_path,
            "line": line,
            "statement": statement,
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


def _goal_matches(net_id: int, window: tuple[int, int] | None,
                  goal_id: int, goal_window: tuple[int, int] | None) -> bool:
    if net_id != goal_id:
        return False
    if goal_window is None:
        return True
    if window is None:
        return False
    return max(window[0], goal_window[0]) <= min(window[1], goal_window[1])


def _path_bfs(cursor, facts: Facts, start_id: int, goal_id: int,
              start_window, goal_window, direction: str,
              ctl_left: int, stop_nets: set[int], max_depth: int = 0):
    """Shortest path via BFS, same pruning as the cone walk. `direction` is
    "forward" (fanout from the start net) or "reverse" (fanin from the start
    net). Returns (trail, precision_lost) where trail is a list of
    (net_id, window, edge) from start to goal, or None."""
    if _goal_matches(start_id, start_window, goal_id, goal_window):
        return [(start_id, start_window, None)], False
    # Reverse search starts at the target.  If the target is a state element
    # and --comb is active, prune it now so the semantics match the forward
    # direction (which would never *enter* this state on the way to the goal).
    if direction == "reverse" and start_id in stop_nets:
        return None, False
    bfs_name = "fanout_bfs" if direction == "forward" else "fanin_bfs"
    start = (start_id, None, ctl_left, start_window)
    visited = {start}
    parents: dict = {}
    frontier = [start]
    precision_lost = False
    steps = 0
    while frontier and (max_depth == 0 or steps < max_depth):
        nets = [n for n, _, _, _ in frontier]
        rows = _arcs_batch(cursor, bfs_name, nets)
        by_signal: dict[int, list[dict]] = {}
        for r in rows:
            by_signal.setdefault(r["near_net_id"], []).append(r)
        next_frontier = []
        for net, ctx, ctl_left, window in frontier:
            for r in by_signal.get(net, []):
                step = _advance(facts, r, ctx, ctl_left, window, stop_nets)
                if step is None:
                    continue
                far_net, far_window, nctx, nctl, widened, unreachable = step
                if unreachable:
                    continue          # a path never routes through dead code
                key = (far_net, nctx, nctl, far_window)
                if key in visited:
                    continue
                visited.add(key)
                row_copy = dict(r)
                row_copy["_widened"] = widened
                parents[key] = ((net, ctx, ctl_left, window), row_copy)
                is_goal = far_net == goal_id
                # Forward cannot enter a state (including a state target).
                if direction == "forward" and far_net in stop_nets:
                    continue
                if _goal_matches(far_net, far_window, goal_id, goal_window):
                    trail = []
                    at = key
                    while at is not None:
                        entry = parents.get(at)
                        if entry is None:
                            trail.append((at[0], at[3], None))
                            break
                        parent_state, edge = entry
                        trail.append((at[0], at[3], edge))
                        at = parent_state
                    return trail[::-1], False
                if goal_window is not None and is_goal and far_window is None:
                    precision_lost = True
                    continue
                # Reverse: source reached without exact match, or a state
                # that is not the source goal, is the end of the walk.
                if direction == "reverse" and (is_goal or far_net in stop_nets):
                    continue
                next_frontier.append(key)
        frontier = next_frontier
        steps += 1
    return None, precision_lost


def path(db: Db, from_sig: str, to_sig: str, max_depth: int = 0,
         no_ctl: bool = False, comb: bool = False, through_latch: bool = False,
         follow_ctl: bool = False, ctl_depth: int | None = None, top: str = "") -> dict:
    cur = db.conn.cursor()
    f = resolve(cur, from_sig, top)
    t = resolve(cur, to_sig, top)
    facts = load_facts(cur)
    ctl_left = _ctl_left(ctl_depth, follow_ctl)
    stop_nets = _stop_nets(facts, comb, through_latch)
    reverse = f.window is None and t.window is not None
    if reverse:
        trail, precision_lost = _path_bfs(
            cur, facts, t.net_id, f.net_id, t.window, f.window, "reverse",
            ctl_left, stop_nets, max_depth)
    else:
        trail, precision_lost = _path_bfs(
            cur, facts, f.net_id, t.net_id, f.window, t.window, "forward",
            ctl_left, stop_nets, max_depth)
    found = trail is not None
    if found and reverse:
        trail = trail[::-1]
        # Each state carried the edge from its search-order parent.  After
        # reversing to source -> target, move each edge onto the state that
        # owns its source side, so the downstream renderer stays direction-agnostic.
        for i in range(len(trail) - 1, 0, -1):
            net, win, _ = trail[i]
            trail[i] = (net, win, trail[i - 1][2])
        trail[0] = (trail[0][0], trail[0][1], None)
    edges = []
    nodes = []
    if found:
        net_seq = [n for n, _, _ in trail]
        names = net_names(cur, net_seq)
        nodes = [names.get(n, f"<net {n}>") for n in net_seq]
        src_reader = Source(cur)
        for (a, a_win, _), (b, b_win, edge) in zip(trail, trail[1:]):
            file_path = edge.get("file_path") if edge else None
            line = edge.get("src_line") if edge else None
            kind = edge.get("edge_kind") if edge else None
            edges.append({
                "source": names.get(edge["src_net_id"], f"<net {edge['src_net_id']}>") if edge else names.get(a, f"<net {a}>"),
                "target": names.get(edge["dst_net_id"], f"<net {edge['dst_net_id']}>") if edge else names.get(b, f"<net {b}>"),
                "source_window": list(a_win) if a_win else None,
                "target_window": list(b_win) if b_win else None,
                "widened": bool(edge.get("_widened")) if edge else False,
                "kind": kind,
                "file": file_path,
                "line": line,
                "statement": src_reader.line(file_path, line)[0] if file_path and line else None,
            })
    reason = "bit_precision_lost" if precision_lost and not found else None
    data = {
        "from": f.full_path,
        "to": t.full_path,
        "from_window": list(f.window) if f.window else None,
        "to_window": list(t.window) if t.window else None,
        "granularity": "bit" if (f.window is not None or t.window is not None) else "net",
        "found": found,
        "reason": reason,
        "length": len(trail) - 1 if found else 0,
        "nodes": nodes,
        "edges": edges,
    }
    summary = {"found": found, "length": len(trail) - 1 if found else 0}
    if reason:
        summary["reason"] = reason
    return {"data": data, "summary": summary, "diagnostics": []}
