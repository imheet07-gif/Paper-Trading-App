"""SQLite journal: every order, fill and mark with a timestamp, for replay."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'default',
    strategy TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,      -- order | fill | status | mark | settle
    order_id INTEGER,
    payload TEXT NOT NULL    -- JSON
);
CREATE INDEX IF NOT EXISTS events_kind ON events(account_id, kind, ts);
"""


class Journal:
    def __init__(self, path: str = ":memory:"):
        # One connection shared across threads (e.g. server loop vs. setup), guarded by a lock.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self.db.executescript(SCHEMA)

    def record(self, ts: datetime, kind: str, payload: dict, order_id: int | None = None,
               account_id: str = "default", strategy: str = "") -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO events (ts, account_id, strategy, kind, order_id, payload)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (ts.isoformat(), account_id, strategy, kind, order_id,
                 json.dumps(payload, default=str)),
            )
            self.db.commit()

    def events(self, kind: str | None = None, account_id: str | None = None) -> list[dict]:
        where, args = [], []
        for col, val in (("kind", kind), ("account_id", account_id)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        sql = "SELECT seq, ts, account_id, strategy, kind, order_id, payload FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            rows = self.db.execute(sql + " ORDER BY seq", args).fetchall()
        return [
            {"seq": s, "ts": t, "account_id": a, "strategy": st, "kind": k,
             "order_id": o, "payload": json.loads(p)}
            for s, t, a, st, k, o, p in rows
        ]
