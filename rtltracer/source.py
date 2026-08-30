"""Quote source lines the export recorded, checking each file still hashes to
what was stored. Shared by trace (commands) and the cone walks (cone)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from rtltracer import sql


def _candidate_paths(src: str, file_path: str) -> list[str]:
    """Paths to try when quoting source, in priority order. The recorded
    src_path is machine-specific; file_path is portable, and normalizing its
    separators also covers fixtures built on the other platform."""
    normalized = file_path.replace("\\", "/")
    return list(dict.fromkeys([src, file_path, normalized]))


def source_state(cursor, file_path: str) -> tuple[str, str, str | None]:
    """(src_path, sha256, state), where state is 'current'/'stale'/'missing'.
    Reads the file once to hash it against the recorded digest."""
    row = cursor.execute(sql.load("trace_source"), {"file_path": file_path}).fetchone()
    if row is None:
        return file_path, "", "missing"
    src = row["src_path"]
    digest = row["sha256"]
    for cand in _candidate_paths(src, file_path):
        try:
            data = Path(cand).read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() == digest:
            return cand, digest, "current"
        return cand, digest, "stale"
    return src, digest, "missing"


class Source:
    """Quotes source lines, hashing each file and holding its split lines once,
    so repeated lookups in one command touch the disk once per file."""

    def __init__(self, cursor):
        self.cursor = cursor
        self._state: dict[str, tuple[str, str, str | None]] = {}
        self._lines: dict[str, list[str] | None] = {}

    def line(self, file_path: str, line: int | None) -> tuple[str | None, str]:
        if file_path not in self._state:
            self._state[file_path] = source_state(self.cursor, file_path)
        src, _digest, state = self._state[file_path]
        if state == "missing" or line is None:
            return None, state
        # stale files are still quoted for display; callers surface the state.
        lines = self._read(file_path, src)
        if lines is None:
            return None, "missing"
        return (lines[line - 1].strip() if 1 <= line <= len(lines) else None), state

    def _read(self, file_path: str, src: str) -> list[str] | None:
        if file_path not in self._lines:
            lines = None
            for cand in _candidate_paths(src, file_path):
                try:
                    lines = Path(cand).read_text(
                        encoding="utf-8", errors="replace").splitlines()
                    break
                except OSError:
                    continue
            self._lines[file_path] = lines
        return self._lines[file_path]
