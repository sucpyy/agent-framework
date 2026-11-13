"""SQLite persistence store — metrics, sessions, auto-migration from JSON."""
import sqlite3, json, os, time
from pathlib import Path
from threading import Lock

_store: "PersistenceStore | None" = None
_lock = Lock()


class PersistenceStore:
    def __init__(self, db_path: str = ".agent_data.db"):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self):
        c = self.conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT, agent TEXT, description TEXT,
                tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
                latency REAL DEFAULT 0, cost REAL DEFAULT 0,
                status TEXT DEFAULT 'completed',
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                name TEXT, message_count INTEGER DEFAULT 0,
                created_at REAL, updated_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_agent ON metrics(agent);
            CREATE INDEX IF NOT EXISTS idx_metrics_created ON metrics(created_at);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
        """)
        self.conn.commit()

    # ─── Metrics ─────────────────────────────
    def record_metric(self, run_id: str, agent: str, description: str,
                      tokens_in: int = 0, tokens_out: int = 0,
                      latency: float = 0, cost: float = 0, status: str = "completed"):
        self.conn.execute(
            "INSERT INTO metrics (run_id,agent,description,tokens_in,tokens_out,latency,cost,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, agent, description, tokens_in, tokens_out, latency, cost, status, time.time()))
        self.conn.commit()

    def get_metrics(self, agent: str | None = None, limit: int = 50) -> list[dict]:
        if agent:
            rows = self.conn.execute(
                "SELECT * FROM metrics WHERE agent=? ORDER BY created_at DESC LIMIT ?", (agent, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM metrics ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_cost_summary(self, days: int = 7) -> dict:
        since = time.time() - days * 86400
        row = self.conn.execute(
            "SELECT COUNT(*) as runs, SUM(tokens_in+ tokens_out) as total_tokens, SUM(cost) as total_cost, AVG(latency) as avg_latency FROM metrics WHERE created_at > ?",
            (since,)).fetchone()
        return dict(row) if row else {}

    # ─── Sessions ────────────────────────────
    def upsert_session(self, session_id: str, name: str = "", message_count: int = 0):
        now = time.time()
        self.conn.execute(
            "INSERT INTO sessions (session_id,name,message_count,created_at,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET name=excluded.name, message_count=excluded.message_count, updated_at=excluded.updated_at",
            (session_id, name, message_count, now, now))
        self.conn.commit()

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_store(db_path: str = ".agent_data.db") -> PersistenceStore:
    global _store
    with _lock:
        if _store is None:
            _store = PersistenceStore(db_path)
        return _store
