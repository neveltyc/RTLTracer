"""Open a design database, check the version gate, read the seal."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rtltracer import sql

SCHEMA_VERSION = 21


@dataclass
class Db:
    path: str
    conn: sqlite3.Connection


class DbError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def open_db(path: str) -> Db:
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as e:
        raise DbError("DB_UNREADABLE", f"cannot open '{path}': {e}")
    conn.row_factory = sqlite3.Row
    db = Db(path=path, conn=conn)
    try:
        row = conn.execute(sql.load("info")).fetchone()
    except sqlite3.Error as e:
        conn.close()
        raise DbError("DB_UNREADABLE", f"'{path}' is not a design database: {e}")
    if row is None:
        conn.close()
        raise DbError("DB_UNREADABLE", f"'{path}' has no v_db_info seal")
    version = row["schema_version"]
    if version != SCHEMA_VERSION:
        conn.close()
        raise DbError(
            "DB_UNREADABLE",
            f"schema v{version} is not readable; this build reads v{SCHEMA_VERSION}. Re-export the RTL with a matching rtl-designdb.",
        )
    return db


def net_names(cursor, net_ids) -> dict[int, str]:
    """Batch net-id -> full-path lookup for result assembly."""
    ids = sorted(set(net_ids))
    if not ids:
        return {}
    text, values = sql.fill("net_names", ids)
    return {r["net_id"]: r["full_path"] for r in cursor.execute(text, values)}
