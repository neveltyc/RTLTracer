"""Load the SQL statements this tool runs. Each file is a named query;
the SQL is the contract, Python only binds parameters and reads rows."""
from __future__ import annotations

import pathlib

_SQL_DIR = pathlib.Path(__file__).parent / "sql"
_cache: dict[str, str] = {}


def load(name: str) -> str:
    if name not in _cache:
        _cache[name] = (_SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    return _cache[name]


def in_list(nets: list[int]) -> str:
    """The ?,?,... placeholder string for an IN list, and its values."""
    return ",".join("?" * len(nets))


def fill(name: str, nets: list[int]) -> tuple[str, list[int]]:
    """Expand a {nets} placeholder into concrete placeholders."""
    text = load(name).replace("{nets}", in_list(nets))
    return text, nets
