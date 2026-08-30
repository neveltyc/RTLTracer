"""Bit-window helpers shared by the cone walk and trace.

All offsets are LSB-relative into the flattened object, as the schema
stores them.  A window is (lo, hi), inclusive; None means the whole net.
"""

# Sentinel: an edge the current bit window does not touch.
SKIP = object()


def propagate(window, cur_lo, cur_hi, cur_exact,
              other_lo, other_hi, other_exact, map_exact):
    """Carry a bit window across one dependency edge.

    Returns SKIP when the window does not touch the edge, None when
    precision is lost (the far side widens to the whole net), or the
    far-side (lo, hi) for an exact correspondence.
    """
    if window is None:
        return None
    wlo, whi = window
    if cur_lo is not None and cur_hi is not None:
        olo, ohi = max(wlo, cur_lo), min(whi, cur_hi)
        if olo > ohi:
            return SKIP                     # disjoint: edge feeds other bits
        overlap = (olo, ohi)
    elif cur_lo is None and cur_hi is None and cur_exact == 1:
        overlap = (wlo, whi)                # whole net covers the window
    else:
        return None                         # unknown extent: widen
    if not (cur_exact == 1 and other_exact == 1 and map_exact == 1):
        return None                         # not an exact bit correspondence
    if cur_lo is not None and other_lo is not None:
        if (cur_hi - cur_lo) != (other_hi - other_lo):
            return None                     # width mismatch
        base_cur, base_other = cur_lo, other_lo
    elif cur_lo is None and other_lo is None:
        base_cur, base_other = 0, 0         # whole to whole, offset-preserving
    else:
        return None                         # mixed whole/partial: widen
    return (base_other + overlap[0] - base_cur,
            base_other + overlap[1] - base_cur)
