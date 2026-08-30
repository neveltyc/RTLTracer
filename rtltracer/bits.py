"""Bit-window helpers shared by the cone walk and trace.

All offsets are LSB-relative into the flattened object, as the schema
stores them.  A window is (lo, hi), inclusive; None means the whole net.
"""

# Sentinel: an edge the current bit window does not touch.
SKIP = object()


def propagate(window, near_lo, near_hi, far_lo, far_hi, map_kind):
    """Carry a bit window across one edge from near to far.

    Returns SKIP when the window does not touch the edge, None when
    precision is lost (the far side widens to the whole net), or the
    far-side (lo, hi) for an exact correspondence.
    """
    if window is None:
        return None
    wlo, whi = window
    if near_lo is not None and near_hi is not None:
        olo, ohi = max(wlo, near_lo), min(whi, near_hi)
        if olo > ohi:
            return SKIP                     # disjoint: edge feeds other bits
        overlap = (olo, ohi)
    elif near_lo is None and near_hi is None:
        overlap = (wlo, whi)                # whole net covers the window
    else:
        return None                         # unknown extent: widen
    if map_kind != "exact":
        return None                         # not an exact bit correspondence
    # exact ⇒ equal-width and offset-preserving. A whole end is implicitly
    # [0, width-1], so its base bit is 0; a concrete end bases at its lo. This
    # matches v_trace_edge, which normalizes a whole exact end the same way.
    if near_lo is not None and far_lo is not None and \
            (near_hi - near_lo) != (far_hi - far_lo):
        return None                         # concrete widths disagree
    near_base = near_lo if near_lo is not None else 0
    far_base = far_lo if far_lo is not None else 0
    return (far_base + overlap[0] - near_base,
            far_base + overlap[1] - near_base)


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent [lo, hi] intervals into a canonical set."""
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def subtract(window: tuple[int, int], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Parts of `window` not already covered (covered is sorted and merged)."""
    lo, hi = window
    parts, cur = [], lo
    for ilo, ihi in covered:
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


def uncovered(covered: dict, key, window: tuple[int, int] | None) -> list:
    """Mark `window` walked for `key`; return the parts not already walked
    (each None for the whole net, or a (lo, hi) range). [] if nothing is new."""
    cur = covered.get(key)
    if cur == "WHOLE":
        return []
    if window is None:
        covered[key] = "WHOLE"
        return [None]
    intervals = cur or []
    parts = subtract(window, intervals)
    if not parts:
        return []
    covered[key] = merge_intervals(intervals + [window])
    return parts
