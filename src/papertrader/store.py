"""SQLite persistence for users, sessions, account state and instruments.

Passwords: scrypt with a per-user salt. Session tokens: random, stored only as
SHA-256 hashes, so a leaked database does not leak live sessions.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .models import Instrument

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,            -- lower-cased name
    name TEXT NOT NULL,
    salt BLOB NOT NULL,
    pw_hash BLOB NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_state (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""
SESSION_TTL = 30 * 24 * 3600
SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


@dataclass(frozen=True)
class User:
    id: str
    name: str


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    # ---- users ---------------------------------------------------------------
    def create_user(self, name: str, password: str) -> User:
        uid, salt = name.lower(), secrets.token_bytes(16)
        pw = hash_password(password, salt)
        try:
            with self.lock:
                self.db.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                                (uid, name, salt, pw, time.time()))
                self.db.commit()
        except sqlite3.IntegrityError:
            raise ValueError("name taken") from None
        return User(uid, name)

    def check_password(self, name: str, password: str) -> User | None:
        with self.lock:
            row = self.db.execute("SELECT id, name, salt, pw_hash FROM users WHERE id = ?",
                                  (name.lower(),)).fetchone()
        # Always hash, so unknown names cost the same time as wrong passwords.
        salt = row[2] if row else b"\0" * 16
        candidate = hash_password(password, salt)
        if row and hmac.compare_digest(candidate, row[3]):
            return User(row[0], row[1])
        return None

    def users(self) -> list[User]:
        with self.lock:
            return [User(i, n) for i, n in self.db.execute("SELECT id, name FROM users ORDER BY created")]

    # ---- sessions --------------------------------------------------------------
    @staticmethod
    def _th(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def new_session(self, user: User, now: float | None = None) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                            (self._th(token), user.id, (now or time.time()) + SESSION_TTL))
            self.db.commit()
        return token

    def user_for_session(self, token: str, now: float | None = None) -> User | None:
        with self.lock:
            row = self.db.execute(
                "SELECT u.id, u.name, s.expires FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = ?", (self._th(token),)).fetchone()
        if not row or row[2] < (now or time.time()):
            return None
        return User(row[0], row[1])

    def end_session(self, token: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (self._th(token),))
            self.db.commit()

    # ---- account state -------------------------------------------------------
    def save_state(self, user_id: str, state: dict) -> None:
        with self.lock:
            self.db.execute("INSERT INTO account_state VALUES (?, ?) ON CONFLICT(user_id)"
                            " DO UPDATE SET state = excluded.state", (user_id, json.dumps(state)))
            self.db.commit()

    def load_state(self, user_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT state FROM account_state WHERE user_id = ?",
                                  (user_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # ---- instruments (kept so old positions stay valid after contracts expire) ---
    def save_instruments(self, instruments: dict[str, Instrument]) -> None:
        rows = [(s, json.dumps({"underlying": i.underlying, "kind": i.kind, "lot_size": i.lot_size,
                                "tick_size": str(i.tick_size),
                                "strike": None if i.strike is None else str(i.strike),
                                "expiry": i.expiry.isoformat() if i.expiry else None}))
                for s, i in instruments.items()]
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO instruments VALUES (?, ?)", rows)
            self.db.commit()

    def load_instruments(self) -> dict[str, Instrument]:
        with self.lock:
            rows = self.db.execute("SELECT symbol, data FROM instruments").fetchall()
        out = {}
        for sym, data in rows:
            d = json.loads(data)
            out[sym] = Instrument(sym, d["underlying"], d["kind"], d["lot_size"], Decimal(d["tick_size"]),
                                  None if d["strike"] is None else Decimal(d["strike"]),
                                  date.fromisoformat(d["expiry"]) if d["expiry"] else None)
        return out
