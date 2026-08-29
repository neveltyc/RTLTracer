"""fanin, fanout, path — the cone commands. BFS engine only, three
pruning rules: state elements (--comb, --through-latch), dead branches
(constant-condition arms), and call-site isolation (admissible /
next_ctx).  Gating: --no-ctl excludes control arcs; --follow-ctl /
--ctl-depth=N follow them instead of stopping at them."""
from __future__ import annotations

from dataclasses import dataclass

from rtltracer import sql
from rtltracer.db import Db
from rtltracer.resolve import resolve
from rtltracer.source import Source


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


_SKIP = object()   # sentinel: an edge the bit window does not touch


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _subtract(window: tuple[int, int], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    lo, hi = window
    parts, cur = [], lo
    for ilo, ihi in covered:            # covered is sorted and merged
        if ihi < cur:
            continue
        if ilo > hi:
            break
        if ilo > cur:
            parts.append((cur, min(ilo - 1, hi)))
        cur = max(cur, ihi + 1)
        if cur > hi:
            break
    if cur <= hi:
        parts.append((cur, hi))
    return parts


def _uncovered(covered: dict, key, window: tuple[int, int] | None) -> list:
    """Mark `window` walked for `key`; return the parts not already walked
    (each None for the whole net, or a (lo, hi) range). [] if nothing is new."""
    cur = covered.get(key)
    if cur == "WHOLE":
        return []
    if window is None:
        covered[key] = "WHOLE"
        return [None]
    intervals = cur or []
    parts = _subtract(window, intervals)
    if not parts:
        return []
    covered[key] = _merge(intervals + [window])
    return parts


def _propagate(window, r: dict):
    """Carry a bit window across one dependency edge. Returns _SKIP when the
    window does not touch the edge, None when precision is lost (widen to the
    whole far net), or the far-side (lo, hi). Offsets are LSB-relative."""
    if window is None:
        return None
    wlo, whi = window
    cur_lo, cur_hi, cur_exact = r.get("cur_lo"), r.get("cur_hi"), r.get("cur_exact")
    if cur_lo is not None and cur_hi is not None:
        olo, ohi = max(wlo, cur_lo), min(whi, cur_hi)
        if olo > ohi:
            return _SKIP                        # disjoint: this edge feeds other bits
        overlap = (olo, ohi)
    elif cur_lo is None and cur_hi is None and cur_exact == 1:
        overlap = (wlo, whi)                     # whole net covers the window
    else:
        return None                             # unknown extent: widen
    if not (cur_exact == 1 and r.get("other_exact") == 1 and r.get("map_exact") == 1):
        return None                             # not an exact bit correspondence
    other_lo, other_hi = r.get("other_lo"), r.get("other_hi")
    if cur_lo is not None and other_lo is not None:
        if (cur_hi - cur_lo) != (other_hi - other_lo):
            return None                         # width mismatch
        base_cur, base_other = cur_lo, other_lo
    elif cur_lo is None and other_lo is None:
        base_cur, base_other = 0, 0             # whole to whole, offset-preserving
    else:
        return None                             # mixed whole/partial: widen
    return (base_other + overlap[0] - base_cur, base_other + overlap[1] - base_cur)


def _cone_bfs(cursor, facts: Facts, name: str, start: int, no_ctl: bool,
              comb: bool, through_latch: bool, follow_ctl: bool,
              ctl_depth: int | None, depth: int, direction: str,
              start_window: tuple[int, int] | None) -> list[dict]:
    """BFS with call-site isolation, state-element and dead-code pruning,
    configurable gating, and bit-window carry. State is
    (net, ctx, ctl_left, window); coverage keeps each net's walked bits."""
    ctl_init = 0 if not follow_ctl and ctl_depth is None else (ctl_depth if ctl_depth is not None else -1)
    covered: dict = {}
    _uncovered(covered, (start, None, ctl_init), start_window)
    frontier = [(start, None, ctl_init, start_window)]
    edges = []
    no = int(no_ctl)
    d = 0
    while frontier and (depth == 0 or d < depth):  # depth 0 walks to closure
        d += 1
        nets = [net for net, _, _, _ in frontier]
        rows = _arcs_batch(cursor, name, nets, no)
        by_signal: dict[int, list[dict]] = {}
        for r in rows:
            key = r["tgt_net_id"] if direction == "driver" else r["src_net_id"]
            by_signal.setdefault(key, []).append(r)

        next_frontier = []
        for net, ctx, ctl_left, window in frontier:
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
                    r["_cur_window"], r["_far_window"], r["_widened"] = window, None, False
                    edges.append(r)
                    continue
                # Carry the bit window across the edge. A condition gates the
                # whole statement, so it is not bit-mappable.
                if is_control:
                    nxt = None
                else:
                    nxt = _propagate(window, r)
                    if nxt is _SKIP:
                        continue          # this arc does not feed the selected bits
                # The end to advance to is the one opposite the frontier net:
                # the driver for a fan-in, the load for a fan-out.
                far = r["src_net_id"] if direction == "driver" else r["tgt_net_id"]
                r["_depth"] = d
                r["ends_at_state"] = _at_state(facts, comb, through_latch, far)
                r["_unreachable"] = _unreachable(cursor, facts, r.get("stmt_id"))
                r["_cur_window"] = window
                r["_far_window"] = nxt
                r["_widened"] = window is not None and nxt is None and not is_control
                edges.append(r)
                if r["_unreachable"] or r["ends_at_state"]:
                    continue
                nctx = _next_ctx(facts, r, ctx, far)
                nctl = ctl_left
                if is_control and ctl_left >= 0:
                    if ctl_left == 0:
                        continue
                    nctl = ctl_left - 1
                for part in _uncovered(covered, (far, nctx, nctl), nxt):
                    next_frontier.append((far, nctx, nctl, part))
        frontier = next_frontier
    return edges


def _walk(cursor, db: Db, signal: str, direction: str, depth: int,
          no_ctl: bool, comb: bool, through_latch: bool,
          follow_ctl: bool, ctl_depth: int | None, top: str) -> dict:
    sig = resolve(cursor, signal, top)
    facts = load_facts(cursor)
    bfs_name = "fanin_bfs" if direction == "driver" else "fanout_bfs"
    raw = _cone_bfs(cursor, facts, bfs_name, sig.net_id, no_ctl, comb,
                    through_latch, follow_ctl, ctl_depth, depth, direction, sig.window)
    if depth > 0:
        raw = [r for r in raw if r["_depth"] <= depth]
    if no_ctl:
        raw = [r for r in raw if r.get("dep_kind") != "control"]
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
        net_ids.add(r["tgt_net_id"])
    names = {}
    if net_ids:
        names = _name_nets(cursor, net_ids)
    src_reader = Source(cursor)
    edges = []
    for r in raw:
        src, tgt = r["src_net_id"], r["tgt_net_id"]
        kind = r.get("driver_kind") or r.get("load_kind")
        file_path, line = r.get("file_path"), r.get("src_line")
        statement = src_reader.line(file_path, line)[0] if file_path and line else None
        # The frontier net's window sits on the near end; the far net's on the
        # other. For a fan-in the frontier is the target, for a fan-out the source.
        cur_win, far_win = r.get("_cur_window"), r.get("_far_window")
        target_win, source_win = (cur_win, far_win) if direction == "driver" else (far_win, cur_win)
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


def _name_nets(cursor, net_ids) -> dict[int, str]:
    text, values = sql.fill("net_names", sorted(net_ids))
    return {r["net_id"]: r["full_path"] for r in cursor.execute(text, values)}


def _path_bfs(cursor, facts: Facts, from_id: int, to_id: int, max_depth: int,
              no_ctl: bool, comb: bool, through_latch: bool,
              follow_ctl: bool, ctl_depth: int | None,
              from_window: tuple[int, int] | None):
    """Shortest path via BFS, same pruning as the cone walk. The start window
    travels forward, pruning arcs it does not feed. Returns the trail as a list
    of (net_id, window) pairs, or None."""
    if from_id == to_id:
        return [(from_id, from_window)]
    ctl_init = 0 if not follow_ctl and ctl_depth is None else (ctl_depth if ctl_depth is not None else -1)
    start = (from_id, None, ctl_init, from_window)
    visited = {start}
    parents: dict = {}
    frontier = [start]
    no = int(no_ctl)
    steps = 0
    while frontier and (max_depth == 0 or steps < max_depth):
        nets = [n for n, _, _, _ in frontier]
        rows = _arcs_batch(cursor, "fanout_bfs", nets, no)
        by_signal: dict[int, list[dict]] = {}
        for r in rows:
            by_signal.setdefault(r["src_net_id"], []).append(r)
        next_frontier = []
        for net, ctx, ctl_left, window in frontier:
            for r in by_signal.get(net, []):
                if not _admissible(facts, r.get("call_site_id"), ctx):
                    continue
                is_control = r.get("dep_kind") == "control"
                if is_control and ctl_left == 0 and ctl_depth is None and not follow_ctl:
                    continue
                if is_control:
                    nxt = None
                else:
                    nxt = _propagate(window, r)
                    if nxt is _SKIP:
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
                key = (far, nctx, nctl, nxt)
                if key in visited:
                    continue
                visited.add(key)
                parents[key] = (net, ctx, ctl_left, window)
                if far == to_id:
                    trail = []
                    at = key
                    while at is not None:
                        trail.append((at[0], at[3]))
                        at = parents.get(at)
                    return trail[::-1]
                next_frontier.append(key)
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
                      no_ctl, comb, through_latch, follow_ctl, ctl_depth, f.window)
    found = trail is not None
    edges = []
    nodes = []
    if found:
        net_seq = [n for n, _ in trail]
        names = _name_nets(cur, net_seq)
        nodes = [names.get(n, f"<net {n}>") for n in net_seq]
        src_reader = Source(cur)
        for (a, a_win), (b, b_win) in zip(trail, trail[1:]):
            r = cur.execute(sql.load("path_edge"),
                            {"signal_net_id": b, "driver_net_id": a}).fetchone()
            file_path = r["file_path"] if r else None
            line = r["src_line"] if r else None
            edges.append({
                "source": names.get(a, f"<net {a}>"),
                "target": names.get(b, f"<net {b}>"),
                "source_window": list(a_win) if a_win else None,
                "target_window": list(b_win) if b_win else None,
                "widened": a_win is not None and b_win is None,
                "kind": r["driver_kind"] if r else None,
                "file": file_path,
                "line": line,
                "statement": src_reader.line(file_path, line)[0] if file_path and line else None,
            })
    data = {
        "from": f.full_path,
        "to": t.full_path,
        "granularity": "bit" if f.window else "net",
        "found": found,
        "length": len(trail) - 1 if found else 0,
        "nodes": nodes,
        "edges": edges,
    }
    summary = {"found": found, "length": len(trail) - 1 if found else 0}
    return {"data": data, "summary": summary, "diagnostics": []}
