"""SQLite journal: every order, fill and mark with a timestamp, for replay."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,      -- order | fill | status | mark | settle
    order_id INTEGER,
    payload TEXT NOT NULL    -- JSON
);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);
"""


class Journal:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def record(self, ts: datetime, kind: str, payload: dict, order_id: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO events (ts, kind, order_id, payload) VALUES (?, ?, ?, ?)",
            (ts.isoformat(), kind, order_id, json.dumps(payload, default=str)),
        )
        self.db.commit()

    def events(self, kind: str | None = None) -> list[dict]:
        sql = "SELECT seq, ts, kind, order_id, payload FROM events"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        rows = self.db.execute(sql + " ORDER BY seq", args).fetchall()
        return [
            {"seq": s, "ts": t, "kind": k, "order_id": o, "payload": json.loads(p)}
            for s, t, k, o, p in rows
        ]
