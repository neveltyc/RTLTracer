"""rebind — re-point src_file.path to files that still match the recorded
digest, so a moved or redistributed database quotes source again. Content hash
is the only identity used: it fixes a lost/wrong index, never a changed file."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from rtltracer.db import Db, DbError
from rtltracer.sql import sql


def _basename(path: str) -> str:
    """Last component of a stored path. The recorded path is the export
    machine's, so it may use Windows separators on a POSIX host; treat '\\' as a
    separator too, matching source.py's candidate normalization."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _digest(path: str) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _index_roots(roots: list[str], wanted: set[str]) -> dict[str, str]:
    """Map digest -> absolute path for every file under the roots whose basename
    the database asks for. The basename prefilter keeps the walk from hashing an
    entire tree; a digest seen twice keeps the first path."""
    by_digest: dict[str, str] = {}
    for root in roots:
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if name not in wanted:
                    continue
                full = os.path.join(dirpath, name)
                digest = _digest(full)
                if digest is not None:
                    by_digest.setdefault(digest, str(Path(full).resolve()))
    return by_digest


def rebind(db: Db, src_roots: list[str]) -> dict:
    for root in src_roots:
        if not os.path.isdir(root):
            raise DbError("REBIND_BAD_ROOT", f"--src-root '{root}' is not a directory")

    cur = db.conn.cursor()
    src_files = cur.execute(sql.load("rebind_src_files")).fetchall()
    wanted = {_basename(r["path"]) for r in src_files}
    by_digest = _index_roots(src_roots, wanted)

    files = []
    matched = 0
    for r in src_files:
        new_path = by_digest.get(r["digest"])
        if new_path is not None:
            cur.execute(sql.load("rebind_update"), {"path": new_path, "id": r["id"]})
            matched += 1
        files.append({
            "basename": _basename(r["path"]),
            "digest": r["digest"],
            "old_path": r["path"],
            "new_path": new_path,
            "rebound": new_path is not None,
        })
    db.conn.commit()

    total = len(src_files)
    unmatched = total - matched
    data = {"path": db.path, "roots": list(src_roots), "files": files}
    summary = {
        "roots": len(src_roots),
        "total": total,
        "matched": matched,
        "rebound": matched,
        "unmatched": unmatched,
        "resolved": matched == total,
    }
    diagnostics = []
    if unmatched:
        n = unmatched
        diagnostics.append({
            "severity": "warning",
            "code": "REBIND_UNMATCHED",
            "message": f"{n} source file{'' if n == 1 else 's'} could not be matched "
                       "by content hash and kept the old path.",
        })
    return {"data": data, "summary": summary, "diagnostics": diagnostics}
