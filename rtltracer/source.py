"""Quote source lines the export recorded, checking each file still hashes to
what was stored. Shared by trace (commands) and the cone walks (cone)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from rtltracer import sql


def source_state(cursor, file_path: str) -> tuple[str, str, str | None]:
    """(src_path, sha256, state), where state is 'current'/'stale'/'missing'.
    Reads the file once to hash it against the recorded digest."""
    row = cursor.execute(sql.load("trace_source"), {"file_path": file_path}).fetchone()
    if row is None:
        return file_path, "", "missing"
    src = row["src_path"]
    digest = row["sha256"]
    try:
        data = Path(src).read_bytes()
    except OSError:
        return src, digest, "missing"
    if hashlib.sha256(data).hexdigest() == digest:
        return src, digest, "current"
    return src, digest, "stale"


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
        if state != "current" or line is None:
            return None, state
        lines = self._read(file_path, src)
        if lines is None:
            return None, "missing"
        return (lines[line - 1].strip() if 1 <= line <= len(lines) else None), state

    def _read(self, file_path: str, src: str) -> list[str] | None:
        if file_path not in self._lines:
            try:
                self._lines[file_path] = Path(src).read_text(
                    encoding="utf-8", errors="replace").splitlines()
            except OSError:
                self._lines[file_path] = None
        return self._lines[file_path]
