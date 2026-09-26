"""Shared blackboard for the agents: SQLite, so state survives restarts and the system knows what's new."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY, source TEXT, kind TEXT, title TEXT, time TEXT,
    data TEXT,                 -- full collected record (json)
    first_seen TEXT, last_seen TEXT,
    triage TEXT,               -- triage agent verdict (json), NULL = not triaged yet
    event_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, title TEXT, category TEXT, region TEXT,
    first_seen TEXT, updated TEXT, status TEXT DEFAULT 'active',
    metrics TEXT,              -- relevance, severity, level, outlets... (json)
    analyzed_metrics TEXT,     -- metrics snapshot at last deep analysis (json)
    analysis TEXT,             -- research brief + market view + critique (json)
    notified_level TEXT, notified_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_log (
    ts TEXT, agent TEXT, event_id TEXT, action TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    ts TEXT, event_id TEXT, level TEXT, path TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    if getattr(_local, "conn", None) is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        _local.conn = c
    return _local.conn


def log(agent: str, action: str, detail=None, event_id: str | None = None):
    db().execute("INSERT INTO agent_log VALUES (?,?,?,?,?)",
                 (now(), agent, event_id, action, json.dumps(detail, default=str) if detail is not None else None))
    db().commit()


def upsert_signal(sig: dict) -> bool:
    """Insert a new signal or refresh last_seen. Returns True if it was new."""
    c = db()
    row = c.execute("SELECT id FROM signals WHERE id=?", (sig["id"],)).fetchone()
    if row:
        c.execute("UPDATE signals SET last_seen=?, data=? WHERE id=?", (now(), json.dumps(sig, default=str), sig["id"]))
        return False
    c.execute("INSERT INTO signals (id, source, kind, title, time, data, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)",
              (sig["id"], sig["source"], sig["kind"], sig["title"], str(sig["time"]), json.dumps(sig, default=str), now(), now()))
    return True


def rows(sql: str, args=()) -> list[dict]:
    out = []
    for r in db().execute(sql, args).fetchall():
        d = dict(r)
        for k in ("data", "triage", "metrics", "analyzed_metrics", "analysis", "detail"):
            if d.get(k):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


def save_event(ev: dict):
    c = db()
    enc = {k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v) for k, v in ev.items()}
    cols = ",".join(enc)
    c.execute(f"INSERT OR REPLACE INTO events ({cols}) VALUES ({','.join('?' * len(enc))})", tuple(enc.values()))
    c.commit()


def get_event(event_id: str) -> dict | None:
    r = rows("SELECT * FROM events WHERE id=?", (event_id,))
    return r[0] if r else None
