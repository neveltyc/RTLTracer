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
    if near_lo is not None and far_lo is not None:
        if (near_hi - near_lo) != (far_hi - far_lo):
            return None
        return (far_lo + overlap[0] - near_lo,
                far_lo + overlap[1] - near_lo)
    if near_lo is None and far_lo is None:
        return overlap                      # whole to whole, offset-preserving
    return None                             # mixed whole/partial: widen
