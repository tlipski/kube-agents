"""Prototype of the store proposed in ../session-kv-decomposition.md.

NOT SHIPPING CODE. It exists to test the design's mechanisms before anyone
implements them in agents/platform/scripts/session_kv_server.py. See README.md.

What is modelled, and where the design specifies it:

  Seam A   db.txn()          -- BEGIN IMMEDIATE for read-modify-write (R6)
           schema + indices  -- versioned DDL, timestamp indices (R8, R9)
  Seam B   create_session()  -- 128-bit ids, idempotency-key replay (R10, R2)
  Seam D   outbox            -- durable queue with step-wise recovery (R2, R11)
  section 5 retention        -- one collector, one window per store (R5)

The HTTP layer is deliberately absent: every claim under test is about storage
semantics, and FastAPI is not installed in this environment anyway.
"""

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

SCHEMA_VERSION = 1

DDL = [
    """CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS session_metadata (
           session_id TEXT PRIMARY KEY,
           metadata   TEXT NOT NULL,
           updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS incidents (
           chat_id    TEXT NOT NULL,
           thread_id  TEXT NOT NULL,
           report     TEXT NOT NULL,
           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
           PRIMARY KEY (chat_id, thread_id))""",
    """CREATE TABLE IF NOT EXISTS alert_quota (
           day TEXT NOT NULL, severity TEXT NOT NULL,
           sent INTEGER NOT NULL DEFAULT 0, suppressed INTEGER NOT NULL DEFAULT 0,
           PRIMARY KEY (day, severity))""",
    """CREATE TABLE IF NOT EXISTS outbox (
           id              INTEGER PRIMARY KEY,
           kind            TEXT NOT NULL,
           idempotency_key TEXT UNIQUE,
           payload         TEXT NOT NULL,
           step            TEXT NOT NULL DEFAULT 'post_alert',
           step_result     TEXT,
           state           TEXT NOT NULL DEFAULT 'pending',
           attempts        INTEGER NOT NULL DEFAULT 0,
           max_attempts    INTEGER NOT NULL DEFAULT 8,
           last_error      TEXT,
           next_attempt_at TIMESTAMP NOT NULL,
           created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    # section 5 / R9: the indices the GC and the ORDER BY need
    """CREATE INDEX IF NOT EXISTS session_metadata_updated ON session_metadata (updated_at)""",
    """CREATE INDEX IF NOT EXISTS incidents_created ON incidents (created_at)""",
    """CREATE INDEX IF NOT EXISTS outbox_due ON outbox (state, next_attempt_at)""",
]

STEPS = ["post_alert", "register_routing", "create_session", "start_turn"]


class Store:
    """The one component that opens the database (Seam A)."""

    def __init__(self, path, journal="TRUNCATE", exclusive=False, timeout=5.0):
        self.path, self.journal, self.exclusive, self.timeout = path, journal, exclusive, timeout

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        try:
            c.execute(f"PRAGMA journal_mode={self.journal}")
            if self.exclusive:
                c.execute("PRAGMA locking_mode=EXCLUSIVE")
                c.execute("BEGIN IMMEDIATE")  # exclusive is lazy; force the lock now
                c.execute("COMMIT")
            yield c
        finally:
            c.close()

    @contextmanager
    def txn(self, c):
        """Seam A: a write lock taken BEFORE the read. The fix for R6."""
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    def migrate(self):
        """Three cases, per Seam A: fresh, unstamped-but-populated, stamped."""
        with self.conn() as c:
            existing = {
                r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for stmt in DDL:
                c.execute(stmt)
            row = c.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                # populated but unstamped -> adopt at v1 rather than re-create
                c.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
                return "adopted" if "session_metadata" in existing else "created"
            return "migrated"

    # -- Seam A: metadata ------------------------------------------------
    def put_metadata(self, session_id, patch):
        """Read-modify-write, but inside txn(). Contrast with the racy version."""
        with self.conn() as c, self.txn(c):
            row = c.execute(
                "SELECT metadata FROM session_metadata WHERE session_id = ?", (session_id,)
            ).fetchone()
            meta = json.loads(row[0]) if row else {}
            meta.update(patch)
            c.execute(
                "INSERT INTO session_metadata (session_id, metadata, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET metadata=excluded.metadata, "
                "updated_at=excluded.updated_at",
                (session_id, json.dumps(meta), _now()),
            )
            return meta

    def metadata(self, session_id):
        with self.conn() as c:
            row = c.execute(
                "SELECT metadata FROM session_metadata WHERE session_id = ?", (session_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    # -- Seam B: session creation ----------------------------------------
    def create_session(self, idempotency_key, metadata=None):
        """128-bit ids (R10) and replay rather than re-execute (R2, R11)."""
        with self.conn() as c, self.txn(c):
            row = c.execute(
                "SELECT payload FROM outbox WHERE idempotency_key = ? AND kind='session'",
                (idempotency_key,),
            ).fetchone()
            if row:
                return json.loads(row[0])["session_id"], "duplicate"
            sid = "k8s-evt-" + secrets.token_hex(16)  # 128 bits
            c.execute(
                "INSERT INTO outbox (kind, idempotency_key, payload, state, next_attempt_at) "
                "VALUES ('session',?,?,'done',?)",
                (idempotency_key, json.dumps({"session_id": sid}), _now()),
            )
            c.execute(
                "INSERT INTO session_metadata (session_id, metadata, updated_at) VALUES (?,?,?)",
                (sid, json.dumps(metadata or {}), _now()),
            )
            return sid, "created"

    # -- Seam D: the outbox ----------------------------------------------
    def enqueue_alert(self, idempotency_key, payload):
        with self.conn() as c, self.txn(c):
            try:
                c.execute(
                    "INSERT INTO outbox (kind, idempotency_key, payload, next_attempt_at) "
                    "VALUES ('alert',?,?,?)",
                    (idempotency_key, json.dumps(payload), _now()),
                )
                return "enqueued"
            except sqlite3.IntegrityError:
                return "duplicate"

    def drain_once(self, do_step, crash_before_commit_at=None):
        """Advance every due row by ONE step, committing each before the next.

        `do_step(step, payload)` performs the side effect. Recovery resumes at
        `step`, so only the step that was in flight is ambiguous (Seam D).
        """
        done = []
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, step, payload FROM outbox WHERE kind='alert' AND state='pending' "
                "ORDER BY next_attempt_at"
            ).fetchall()
            for rid, step, payload in rows:
                result = do_step(step, json.loads(payload))
                if crash_before_commit_at == step:
                    raise RuntimeError(f"simulated crash after doing {step}, before commit")
                nxt = STEPS.index(step) + 1
                with self.txn(c):
                    if nxt >= len(STEPS):
                        c.execute(
                            "UPDATE outbox SET state='done', step_result=? WHERE id=?",
                            (json.dumps(result), rid),
                        )
                    else:
                        c.execute(
                            "UPDATE outbox SET step=?, step_result=?, attempts=attempts+1 WHERE id=?",
                            (STEPS[nxt], json.dumps(result), rid),
                        )
                done.append(step)
        return done

    def outbox_stats(self):
        with self.conn() as c:
            return dict(
                c.execute("SELECT state, COUNT(*) FROM outbox WHERE kind='alert' GROUP BY state")
            )

    # -- section 5: retention --------------------------------------------
    def collect(self, session_days, incident_days, quota_days):
        """One collector, one window per store. Contrast with R5's two owners."""
        with self.conn() as c, self.txn(c):
            a = c.execute(
                "DELETE FROM session_metadata WHERE updated_at < datetime('now', ?)",
                (f"-{session_days} days",),
            ).rowcount
            b = c.execute(
                "DELETE FROM incidents WHERE created_at < datetime('now', ?)",
                (f"-{incident_days} days",),
            ).rowcount
            d = c.execute(
                "DELETE FROM alert_quota WHERE day < date('now', ?)", (f"-{quota_days} days",)
            ).rowcount
            return {"sessions": a, "incidents": b, "quota": d}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def legacy_ddl(path):
    """The pre-decomposition schema, as store.py hand-rolls it -- no
    schema_version row. Seam A's 'adopt a database nobody stamped' fixture."""
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE IF NOT EXISTS session_metadata ("
        "session_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    c.commit()
    c.close()
