"""Resolve a hierarchical signal path to a net id, using the database's
own tree structure — no string suffix matching. Each level of the path
is a `v_tree_node` lookup; generate segments fold into the net name;
escaped identifiers split by LRM 5.6.1 rules."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rtltracer import sql

_SEPS = {".", "/"}
_BIT_SELECT = re.compile(r"^(.*)\[(\d+)(?::(\d+))?\]$")
_OFFSET_SELECT = re.compile(r"^(.*)@\[(\d+)(?::(\d+))?\]$")
_RANGE = re.compile(r"\[(\d+)\s*:\s*(\d+)\]")


@dataclass
class ResolvedSignal:
    net_id: int
    inst_id: int
    net_name: str
    node_path: str
    full_path: str
    width: int | None
    spell: str | None = None
    # The selected bits as flattened LSB-relative offsets, closed [lo, hi];
    # None means the whole net. This is what the cone walk carries.
    window: tuple[int, int] | None = None


class ResolveError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _segments(path: str) -> list[str]:
    out, cur = [], ""
    for ch in path:
        if ch in _SEPS:
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _levels_of(path: str) -> list[str]:
    """Split a path into levels. An escaped identifier may hold a
    separator; LRM 5.6.1 ends it at whitespace, so the segments that
    belong to one level are joined back with '.'."""
    segs = _segments(path)
    levels = []
    i = 0
    while i < len(segs):
        if segs[i].startswith("\\") and not segs[i][-1:].isspace():
            j = i + 1
            while j < len(segs) and not segs[j][-1:].isspace():
                j += 1
            levels.append(".".join(segs[i:j + 1]) if j < len(segs) else ".".join(segs[i:j]))
            i = j + 1 if j < len(segs) else j
        else:
            levels.append(segs[i])
            i += 1
    return levels


def _parse_declared_range(data_type: str | None, width: int | None) -> tuple[int, int] | None:
    if data_type is None or width is None:
        return None
    m = _RANGE.search(data_type)
    if m is None:
        return None
    left, right = int(m.group(1)), int(m.group(2))
    return (left, right) if abs(left - right) + 1 == width else None


def _offsets_of(select: tuple[int, int], decl: tuple[int, int]) -> tuple[int, int]:
    left, right = decl
    low, high = min(left, right), max(left, right)
    if select[0] != select[1] and (select[0] > select[1]) != (left > right):
        raise ResolveError("BAD_SELECT",
                           f"select [{select[0]}:{select[1]}] runs opposite to declared range [{left}:{right}]")
    def to_off(i: int):
        if i < low or i > high:
            raise ResolveError("BAD_SELECT", f"bit {i} outside declared range [{left}:{right}]")
        return (i - right) if left >= right else (right - i)
    a, b = to_off(select[0]), to_off(select[1])
    return (min(a, b), max(a, b))


def split_select(path: str) -> tuple[str, tuple[str, int, int] | None]:
    """Split a trailing bit-select off a path. Returns (base, select), where
    select is ('decl', a, b) for a declared index `sig[a:b]`, ('offset', a, b)
    for a raw flattened offset `sig@[a:b]`, or None."""
    m = _OFFSET_SELECT.match(path)
    if m is not None:
        base, a, b = m.group(1), int(m.group(2)), m.group(3)
        return base, ("offset", a, a if b is None else int(b))
    m = _BIT_SELECT.match(path)
    if m is not None:
        base, a, b = m.group(1), int(m.group(2)), m.group(3)
        return base, ("decl", a, a if b is None else int(b))
    return path, None


def _resolve_select(select, net) -> tuple[str | None, tuple[int, int] | None]:
    """Turn a parsed select into (display spell, flattened window). A declared
    index needs the net's single declared range; a raw @[..] offset does not."""
    if select is None:
        return None, None
    kind, a, b = select
    if kind == "offset":
        spell = f"@[{a}]" if a == b else f"@[{a}:{b}]"
        return spell, (min(a, b), max(a, b))
    decl = _parse_declared_range(net["data_type"], net["width"])
    if decl is None:
        raise ResolveError("BAD_SELECT",
                           f"{net['net_name']} has no single declared range; "
                           f"use @[lo:hi] with flattened bit offsets")
    window = _offsets_of((a, b), decl)   # also range-checks a and b
    spell = f"[{a}]" if a == b else f"[{a}:{b}]"
    return spell, window


def _candidates(cursor, node: int, inst: int | None) -> list[str]:
    kids = [r["node_name"] for r in cursor.execute(sql.load("children_of"), {"parent": node})]
    if inst is not None:
        kids += [r["net_name"] for r in cursor.execute(sql.load("nets_of"), {"inst_id": inst})]
    return sorted(set(kids))


def _child(cursor, node: int, name: str):
    return cursor.execute(sql.load("child_node"), {"parent": node, "name": name}).fetchone()


def _walk(cursor, root: int, levels: list[str], leaf: str) -> dict | None:
    """Walk the tree down `levels`, then find `leaf` as a net of the
    instance reached. Returns None when the path does not resolve."""
    root_node = cursor.execute(sql.load("node_by_id"), {"node_id": root}).fetchone()
    if root_node is None:
        return None
    node = root_node["node_id"]
    inst = root_node["inst_id"]
    below: list[str] = []
    folded: list[str] = []

    i = 0
    while i < len(levels):
        level = levels[i]
        child = _child(cursor, node, level)
        if child is None:
            # Not a tree level: a subroutine scope, an escaped net name, or
            # a generate net carried on the enclosing instance. Everything
            # from here on folds into the local net name.
            if folded:
                return None
            rest = levels[i:]
            break
        kind = child["node_kind"]
        if kind in ("instance", "root"):
            below.extend(folded)
            folded = []
            below.append(child["node_name"])
            node = child["node_id"]
            inst = child["inst_id"]
        elif kind == "generate":
            folded.append(child["node_name"])
            node = child["node_id"]
        else:
            return None  # primitive, unresolved — nothing below it
        i += 1
    else:
        rest = []

    local = ".".join(folded + rest + [leaf])
    row = cursor.execute(sql.load("net_by_name"), {"inst_id": inst, "name": local}).fetchone()
    if row is not None:
        return {"net": dict(row), "below": below, "local": local, "inst": inst}
    if local.startswith("\\"):
        bare = local[1:].rstrip()
        row = cursor.execute(sql.load("net_by_name"), {"inst_id": inst, "name": bare}).fetchone()
        if row is not None:
            return {"net": dict(row), "below": below, "local": bare, "inst": inst}
    return None


def _above_the_design(cursor, root: int, root_inst: int | None, root_name: str,
                      levels: list[str]) -> int | None:
    for i, level in enumerate(levels):
        if level == root_name:
            return i
        if _child(cursor, root, level) is not None:
            return i
        if i + 1 == len(levels) and root_inst is not None:
            net = cursor.execute(sql.load("net_exists"),
                                 {"inst_id": root_inst, "name": level}).fetchone()
            if net is not None:
                return i
    return None


def _diagnose(cursor, root: int, inst: int | None, levels: list[str]) -> dict:
    node = root
    valid = []
    for level in levels:
        child = _child(cursor, node, level)
        if child is not None and child["node_kind"] in ("instance", "root", "generate"):
            valid.append(level)
            node = child["node_id"]
            if child["node_kind"] in ("instance", "root"):
                inst = child["inst_id"]
        else:
            return {"valid_prefix": valid, "failing_segment": level,
                    "candidates": _candidates(cursor, node, inst)}
    return {"valid_prefix": valid, "failing_segment": "leaf",
            "candidates": _candidates(cursor, node, inst)}


def resolve(cursor, signal: str, top: str = "") -> ResolvedSignal:
    base_path, select = split_select(signal)
    all_levels = _levels_of(base_path)
    if not all_levels:
        raise ResolveError("SIGNAL_NOT_FOUND", f"empty path '{signal}'")

    roots = cursor.execute(sql.load("tops")).fetchall()
    if not roots:
        raise ResolveError("NO_TOP", "this database elaborated no top")
    if top:
        root_row = next((r for r in roots if r["node_name"] == top), None)
        if root_row is None:
            names = [r["node_name"] for r in roots]
            raise ResolveError("NO_TOP", f"no top named '{top}'; elaborated tops: {names}")
    else:
        root_row = roots[0]
    root_id, root_name = root_row["node_id"], root_row["node_name"]
    root = cursor.execute(sql.load("node_by_id"), {"node_id": root_id}).fetchone()
    root_inst = root["inst_id"] if root else None

    above = _above_the_design(cursor, root_id, root_inst, root_name, all_levels)
    reached = above is not None
    rest = all_levels[above:] if above is not None else all_levels
    if rest and rest[0] == root_name:
        rest = rest[1:]

    if not rest:
        raise ResolveError("SIGNAL_NOT_FOUND",
                           f"'{signal}' names only the root; try "
                           f"{_candidates(cursor, root_id, root_inst)[:10]}")

    # Try every split of the remaining levels into (tree prefix, leaf name).
    # The leaf may itself contain dots (generate/subroutine segments, an
    # escaped identifier), so the split that resolves wins.
    for split in range(len(rest) - 1, -1, -1):
        result = _walk(cursor, root_id, rest[:split], ".".join(rest[split:]))
        if result is not None:
            net = result["net"]
            below = result["below"]
            local = result["local"]
            full_path = ".".join([root_name] + below + [local])
            spell, window = _resolve_select(select, net)
            return ResolvedSignal(
                net_id=net["net_id"],
                inst_id=net["inst_id"],
                net_name=net["net_name"],
                node_path=".".join([root_name] + below),
                full_path=full_path + (spell or ""),
                width=net["width"],
                spell=spell,
                window=window,
            )

    if not reached:
        raise ResolveError("SIGNAL_NOT_FOUND",
                           f"'{signal}' does not name a signal here; no level of the path matched this design")
    diag = _diagnose(cursor, root_id, root_inst, rest)
    raise ResolveError("SIGNAL_NOT_FOUND",
                       f"'{signal}' does not name a signal: "
                       f"'{diag['failing_segment']}' is not at "
                       f"{'.'.join([root_name] + diag['valid_prefix']) or root_name}. "
                       f"Try: {diag['candidates'][:10]}")
