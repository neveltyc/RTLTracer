"""SQL query registry. In the multi-file package each query is a file
sql/<name>.sql; the bundled single file embeds the same texts instead.
SQL performs indexed fact lookup; Python implements traversal semantics."""
from __future__ import annotations

import pathlib


class _Sql:
    """Load a named SQL query. Disk-backed in the package (lazy, cached);
    the bundler builds one from an embedded {name: text} map instead."""

    def __init__(self, texts: dict[str, str] | None = None):
        self._cache: dict[str, str] = {} if texts is None else dict(texts)

    def load(self, name: str) -> str:
        if name not in self._cache:
            path = pathlib.Path(__file__).parent / "sql" / f"{name}.sql"
            self._cache[name] = path.read_text(encoding="utf-8")
        return self._cache[name]

    def in_list(self, nets: list[int]) -> str:
        """The ?,?,... placeholder string for an IN list."""
        return ",".join("?" * len(nets))

    def fill(self, name: str, nets: list[int]) -> tuple[str, list[int]]:
        """Expand a {nets} placeholder into concrete placeholders."""
        return self.load(name).replace("{nets}", self.in_list(nets)), nets


sql = _Sql()
