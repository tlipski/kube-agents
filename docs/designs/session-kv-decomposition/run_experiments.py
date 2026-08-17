#!/usr/bin/env python3
"""Experiments backing ../session-kv-decomposition.md.

Each asserts, so this exits non-zero if a claim in the design stops holding.

    python3 run_experiments.py            # all
    python3 run_experiments.py K1 K6      # a subset

Standard library only. FastAPI is not installed here, so K9 models the
threadpool rather than reproducing it, and says so.
"""

import concurrent.futures as cf
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from client import NEGATIVE_TTL, NO_THREAD_FLOOR, SessionKVClient  # noqa: E402
from session_kv import STEPS, Store, legacy_ddl  # noqa: E402

RESULTS = []


def record(eid, claim, detail=""):
    RESULTS.append((eid, claim))
    print(f"\n  [{eid}] ok: {claim}")
    for line in str(detail).strip().splitlines():
        print(f"        {line}")


def tmpdb(d, name="kv.db"):
    return os.path.join(d, name)


# ------------------------------------------------------------------ K1
def k1():
    """R6: read-modify-write without BEGIN IMMEDIATE loses a field."""

    def racy_writer(path, sid, key, value, barrier):
        c = sqlite3.connect(path, timeout=10)
        row = c.execute(
            "SELECT metadata FROM session_metadata WHERE session_id=?", (sid,)
        ).fetchone()
        meta = json.loads(row[0])
        barrier.wait(5)  # both have now read the same snapshot
        meta[key] = value
        c.execute(
            "UPDATE session_metadata SET metadata=? WHERE session_id=?", (json.dumps(meta), sid)
        )
        c.commit()
        c.close()

    with tempfile.TemporaryDirectory() as d:
        # -- today's shape: two deferred read-modify-writes ---------------
        p = tmpdb(d, "racy.db")
        s = Store(p)
        s.migrate()
        with s.conn() as c:
            c.execute(
                "INSERT INTO session_metadata (session_id, metadata) VALUES ('s1','{}')"
            )
        bar = threading.Barrier(2)
        ts = [
            threading.Thread(target=racy_writer, args=(p, "s1", k, v, bar))
            for k, v in (("thread_id", "T"), ("user", "U"))
        ]
        [t.start() for t in ts]
        [t.join() for t in ts]
        racy = s.metadata("s1")

        # -- the design's txn(): BEGIN IMMEDIATE before the read ----------
        p2 = tmpdb(d, "safe.db")
        s2 = Store(p2)
        s2.migrate()
        s2.put_metadata("s1", {})
        with cf.ThreadPoolExecutor(2) as ex:
            list(
                ex.map(
                    lambda kv: s2.put_metadata("s1", {kv[0]: kv[1]}),
                    [("thread_id", "T"), ("user", "U")],
                )
            )
        safe = s2.metadata("s1")

    assert len(racy) == 1, f"expected the racy path to lose a field, got {racy}"
    assert safe == {"thread_id": "T", "user": "U"}, f"txn() should keep both, got {safe}"
    record(
        "K1",
        "R6 is real: a deferred read-modify-write loses a concurrent field",
        f"today's shape  -> {racy}   (one write silently overwrote the other)\n"
        f"Seam A txn()   -> {safe}",
    )


# ------------------------------------------------------------------ K2
def k2():
    """Design section 2's journal-mode constraint -- which this FALSIFIED.

    The section claimed the mode is a property of the file, that the last setter
    wins, and that disagreeing openers "flip the file back and forth under each
    other". Measured, SQLite does none of those three things symmetrically.
    """
    with tempfile.TemporaryDirectory() as d:

        def fresh(name):
            p = tmpdb(d, name)
            c = sqlite3.connect(p)
            c.execute("CREATE TABLE t (x)")
            c.commit()
            return p, c

        # (a) WAL persists across connections; TRUNCATE does not
        p, a = fresh("a.db")
        a.execute("PRAGMA journal_mode=WAL")
        a.close()
        b = sqlite3.connect(p)
        b.execute("PRAGMA journal_mode=TRUNCATE")
        b.close()
        c = sqlite3.connect(p)
        persisted = c.execute("PRAGMA journal_mode").fetchone()[0]
        c.close()

        # (b) leaving WAL while another connection is open is REFUSED
        p, a = fresh("b.db")
        a.execute("PRAGMA journal_mode=WAL")
        a.execute("INSERT INTO t VALUES (1)")
        a.commit()
        b = sqlite3.connect(p, timeout=0.5)
        try:
            b.execute("PRAGMA journal_mode=TRUNCATE").fetchone()[0]
            leaving = "succeeded"
        except sqlite3.OperationalError as exc:
            leaving = f"{type(exc).__name__}: {exc}"
        b.close()
        a.close()

        # (c) ENTERING WAL while another connection is open succeeds
        p, a = fresh("c.db")
        a.execute("PRAGMA journal_mode=TRUNCATE")
        a.execute("INSERT INTO t VALUES (1)")
        a.commit()
        b = sqlite3.connect(p, timeout=0.5)
        entering = b.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        b.close()
        a.close()
        c = sqlite3.connect(p)
        after_entering = c.execute("PRAGMA journal_mode").fetchone()[0]
        c.close()

        # (d) both setting WAL -- today's actual state -- is uneventful
        p, a = fresh("d.db")
        a.execute("PRAGMA journal_mode=WAL")
        b = sqlite3.connect(p)
        both = b.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        shm = os.path.exists(p + "-shm")
        b.close()
        a.close()

    assert persisted == "delete", (
        f"the design says the mode is a property of the file; TRUNCATE did not persist "
        f"(a third connection sees {persisted!r})"
    )
    assert "locked" in leaving or "busy" in leaving.lower(), (
        f"the design says disagreeing openers flip the mode back and forth; leaving WAL "
        f"while another connection is open actually {leaving}"
    )
    assert entering == "wal" and after_entering == "wal"
    assert both == "wal" and shm
    record(
        "K2",
        "section 2's journal-mode constraint was wrong; the real rule is asymmetric",
        f"(a) WAL persists, TRUNCATE does not : after WAL then TRUNCATE, a third "
        f"connection sees {persisted!r}\n"
        f"(b) leaving WAL with a peer open    : {leaving}\n"
        f"(c) entering WAL with a peer open   : {entering!r}, and it sticks ({after_entering!r})\n"
        f"(d) both setting WAL (today)        : {both!r}, -shm present: {shm}\n"
        "so openers cannot oscillate -- the WAL->other direction is simply refused, and\n"
        "the conversion in phase 1 needs a window with no other opener",
    )


# ------------------------------------------------------------------ K3
def k3():
    """Section 4.1: locking_mode=EXCLUSIVE locks every other opener out.

    This is why exclusivity cannot ship before the last direct opener is ported.
    """
    with tempfile.TemporaryDirectory() as d:
        p = tmpdb(d, "x.db")
        owner = Store(p, journal="TRUNCATE")
        owner.migrate()

        holder = sqlite3.connect(p, isolation_level=None)
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO session_metadata (session_id, metadata) VALUES ('a','{}')")
        holder.execute("COMMIT")  # EXCLUSIVE keeps the lock across the commit

        other = sqlite3.connect(p, timeout=0.5)
        try:
            other.execute("SELECT COUNT(*) FROM session_metadata").fetchone()
            locked_out = False
            err = "none"
        except sqlite3.OperationalError as exc:
            locked_out, err = True, str(exc)
        other.close()
        holder.close()

        after = sqlite3.connect(p, timeout=1)
        reopened = after.execute("SELECT COUNT(*) FROM session_metadata").fetchone()[0]
        after.close()

    assert locked_out, "EXCLUSIVE should lock a second opener out -- it did not"
    assert reopened == 1, "the store must be readable again once the holder closes"
    record(
        "K3",
        "locking_mode=EXCLUSIVE locks out every other opener, as section 4.1 warns",
        f"second opener while held : {err}\n"
        f"after the holder closes  : readable again ({reopened} row)\n"
        "so store.py/bridge.py/session_manager.py must be ported BEFORE phase 6",
    )


# ------------------------------------------------------------------ K4
def k4():
    """R5: two retention owners on one table, and the shorter window wins."""
    with tempfile.TemporaryDirectory() as d:
        p = tmpdb(d, "r.db")
        s = Store(p)
        s.migrate()
        with s.conn() as c:
            for age in (3, 10, 20):
                c.execute(
                    "INSERT INTO session_metadata (session_id, metadata, updated_at) "
                    "VALUES (?,?,datetime('now',?))",
                    (f"s{age}", "{}", f"-{age} days"),
                )
        # the server believes it keeps 14 days
        with s.conn() as c:
            kept_by_server = [
                r[0]
                for r in c.execute(
                    "SELECT session_id FROM session_metadata "
                    "WHERE updated_at >= datetime('now','-14 days')"
                )
            ]
            # store.py runs its own 7-day delete on every write
            c.execute("DELETE FROM session_metadata WHERE updated_at < datetime('now','-7 days')")
            surviving = [r[0] for r in c.execute("SELECT session_id FROM session_metadata")]

    lost = sorted(set(kept_by_server) - set(surviving))
    assert lost == ["s10"], f"expected the 10-day row to be lost to the 7-day owner, got {lost}"
    record(
        "K4",
        "R5 is real: the 7-day owner deletes rows the 14-day owner believes it keeps",
        f"server's 14-day view kept : {sorted(kept_by_server)}\n"
        f"after store.py's 7-day GC : {sorted(surviving)}\n"
        f"silently lost             : {lost}",
    )


# ------------------------------------------------------------------ K5
def k5():
    """R9: without an index the GC DELETE and the ORDER BY are full scans."""
    with tempfile.TemporaryDirectory() as d:
        plans = {}
        for name, indexed in (("today", False), ("Seam A", True)):
            p = tmpdb(d, f"{indexed}.db")
            c = sqlite3.connect(p)
            c.execute(
                "CREATE TABLE session_metadata (session_id TEXT PRIMARY KEY, "
                "metadata TEXT, updated_at TIMESTAMP)"
            )
            if indexed:
                c.execute("CREATE INDEX sm_updated ON session_metadata (updated_at)")
            plan = c.execute(
                "EXPLAIN QUERY PLAN SELECT session_id FROM session_metadata "
                "WHERE updated_at < datetime('now','-14 days')"
            ).fetchall()
            plans[name] = " ".join(r[-1] for r in plan)
            c.close()

    assert "SCAN" in plans["today"], f"expected a full scan today, got {plans['today']}"
    assert "SEARCH" in plans["Seam A"], f"expected an index search, got {plans['Seam A']}"
    record(
        "K5",
        "R9 is real: the retention predicate is a full table scan without the index",
        f"today  : {plans['today']}\nSeam A : {plans['Seam A']}",
    )


# ------------------------------------------------------------------ K6
def k6():
    """R10: a 32-bit id collision is a 500; 128-bit + idempotency replays."""
    with tempfile.TemporaryDirectory() as d:
        p = tmpdb(d, "id.db")
        s = Store(p)
        s.migrate()
        # a collision on today's plain INSERT raises rather than retrying
        with s.conn() as c:
            c.execute("INSERT INTO session_metadata (session_id, metadata) VALUES ('k8s-evt-dead','{}')")
            try:
                c.execute(
                    "INSERT INTO session_metadata (session_id, metadata) VALUES ('k8s-evt-dead','{}')"
                )
                raised = "none"
            except sqlite3.IntegrityError as exc:
                raised = type(exc).__name__

        a, st_a = s.create_session("evt-1")
        b, st_b = s.create_session("evt-1")  # the caller's retry across a failover
        c_, st_c = s.create_session("evt-2")

    assert raised == "IntegrityError", "a duplicate id should raise, which becomes a 500"
    assert (st_a, st_b) == ("created", "duplicate") and a == b, "replay must return the same id"
    assert c_ != a and len(a) == len("k8s-evt-") + 32, "ids should be 128-bit and distinct"
    record(
        "K6",
        "R10 is real, and the idempotency key replays instead of re-executing",
        f"duplicate id on a plain INSERT : {raised} (a 500, not a retry)\n"
        f"create(evt-1) -> {st_a}, create(evt-1) again -> {st_b}, same id: {a == b}\n"
        f"id width: {len(a) - len('k8s-evt-')} hex chars = 128 bits",
    )


# ------------------------------------------------------------------ K7
def k7():
    """R2: in-memory background work is lost on restart; the outbox resumes."""
    performed = []

    def do_step(step, payload):
        performed.append(step)
        return {"ok": step}

    with tempfile.TemporaryDirectory() as d:
        # -- today: BackgroundTasks holds the flow in memory --------------
        pending = ["post_alert", "register_routing", "create_session", "start_turn"]
        in_memory_done = [pending.pop(0)]  # the process dies here
        lost = pending  # nothing on disk knows these were owed

        # -- the design: a durable row, one step per commit ---------------
        p = tmpdb(d, "o.db")
        s = Store(p)
        s.migrate()
        s.enqueue_alert("alert-1", {"cluster": "c1"})
        s.drain_once(do_step)  # post_alert
        try:
            s.drain_once(do_step, crash_before_commit_at="register_routing")
        except RuntimeError:
            pass
        crashed_at = None
        with s.conn() as c:
            crashed_at = c.execute("SELECT step FROM outbox WHERE kind='alert'").fetchone()[0]
        while s.outbox_stats().get("pending"):
            s.drain_once(do_step)
        stats = s.outbox_stats()

    assert lost == ["register_routing", "create_session", "start_turn"]
    assert crashed_at == "register_routing", f"recovery must resume at the in-flight step, got {crashed_at}"
    assert stats == {"done": 1}, f"the alert should complete after the crash, got {stats}"
    record(
        "K7",
        "R2 is real, and step-wise recovery resumes at the in-flight step",
        f"BackgroundTasks after a restart : did {in_memory_done}, silently lost {lost}\n"
        f"outbox after the same crash     : row still at step={crashed_at!r}, then drains to {stats}\n"
        f"steps actually performed        : {performed}\n"
        "note register_routing ran twice -- at-least-once, which is why the design\n"
        "says post_alert is the one step not retried when it may have landed",
    )


# ------------------------------------------------------------------ K8
def k8():
    """Seam A's cache: write-through, fail-open, negative caching, thread_id."""
    now = [0.0]
    server = {}

    def transport(sid):
        if sid == "boom":
            raise ConnectionRefusedError("server down")
        return server.get(sid)

    cl = SessionKVClient(transport, clock=lambda: now[0])

    # write-through: the very first span of a session is a hit, no fetch at all
    cl.put_session_metadata("s1", {"user": "u", "thread_id": "T"})
    first_span = cl.session_metadata("s1")
    assert first_span is not None and cl.stats["fetches"] == 0, "write-through must avoid a fetch"

    # a miss fails open and does not raise
    miss = cl.session_metadata("unknown")
    assert miss is None

    # negative caching: repeated misses for an unknown id do not fetch per span
    cl.refresh("unknown")  # caches None
    before = cl.stats["fetches"]
    for _ in range(5):
        cl.session_metadata("unknown")
    assert cl.stats["fetches"] == before, "a negative entry must not fetch per span"

    # a write clears the negative entry
    cl.put_session_metadata("unknown", {"user": "late", "thread_id": "T2"})
    assert cl.session_metadata("unknown") is not None

    # thread_id: an entry cached without one is re-fetched past the floor
    cl.put_session_metadata("s2", {"user": "u"})  # no thread_id yet
    assert cl.session_metadata("s2") is not None, "fresh entry should hit"
    now[0] += NO_THREAD_FLOOR + 0.1
    assert cl.session_metadata("s2") is None, "past the floor it must re-fetch, not be trusted"
    server["s2"] = {"user": "u", "thread_id": "T3"}
    cl.refresh("s2")
    got = cl.session_metadata("s2")
    assert got and got["thread_id"] == "T3", "the amended thread_id must become visible"

    # the server being down degrades to fail-open rather than raising
    cl.refresh("boom")
    assert cl.stats["fail_open"] == 1

    record(
        "K8",
        "the Seam A cache has all four properties the design specifies",
        f"write-through   : first span hit with {cl.stats['fetches']} fetches at that point\n"
        f"negative caching: 5 spans for an unknown id caused 0 extra fetches\n"
        f"thread_id       : trusted for {NO_THREAD_FLOOR}s, then re-fetched; amended value visible\n"
        f"fail-open       : transport raised, client returned None ({cl.stats['fail_open']} swallowed)",
    )


# ------------------------------------------------------------------ K9
def k9():
    """R4, MODELLED: a bounded sync pool starves fast callers behind slow ones.

    AnyIO is not installed here, so this models FastAPI's 40-slot sync-endpoint
    threadpool with a ThreadPoolExecutor of the same shape. It demonstrates the
    mechanism, not the framework.
    """
    POOL, SLOW, FAST_TIMEOUT = 4, 1.0, 0.25
    pool = cf.ThreadPoolExecutor(POOL)
    try:
        slow = [pool.submit(time.sleep, SLOW) for _ in range(POOL)]
        t0 = time.monotonic()
        fast = pool.submit(lambda: "incident context")
        try:
            fast.result(timeout=FAST_TIMEOUT)
            starved = False
        except cf.TimeoutError:
            starved = True
        waited = time.monotonic() - t0
        [f.result() for f in slow]
    finally:
        pool.shutdown(wait=True)

    assert starved, "a saturated pool should make the fast caller miss its deadline"
    record(
        "K9",
        "R4's mechanism, modelled: a saturated sync pool starves the 2 s incident lookup",
        f"pool={POOL} slots, all held for {SLOW}s; the fast call missed a {FAST_TIMEOUT}s "
        f"deadline after {waited:.2f}s\n"
        "MODEL, not a reproduction: FastAPI/AnyIO are not installed here. The real pool is\n"
        "40 slots and the real holder is `hermes send` with no timeout at all",
    )


# ------------------------------------------------------------------ K10
def k10():
    """Seam A: adopting a database nobody stamped."""
    with tempfile.TemporaryDirectory() as d:
        # fresh
        fresh = Store(tmpdb(d, "fresh.db")).migrate()
        # populated by the old store.py DDL, no schema_version anywhere
        p = tmpdb(d, "legacy.db")
        legacy_ddl(p)
        c = sqlite3.connect(p)
        c.execute("INSERT INTO session_metadata (session_id, metadata) VALUES ('old','{\"a\":1}')")
        c.commit()
        c.close()
        s = Store(p)
        adopted = s.migrate()
        survived = s.metadata("old")
        again = s.migrate()

    assert fresh == "created"
    assert adopted == "adopted", f"an unstamped populated database must be adopted, got {adopted}"
    assert survived == {"a": 1}, "adoption must not destroy existing rows"
    assert again == "migrated"
    record(
        "K10",
        "Seam A's three migration cases, including the one a fresh-database test misses",
        f"no tables            -> {fresh}\n"
        f"tables, no stamp     -> {adopted}, existing row intact: {survived}\n"
        f"stamped              -> {again}",
    )


EXPERIMENTS = {f"K{i}": f for i, f in enumerate([k1, k2, k3, k4, k5, k6, k7, k8, k9, k10], 1)}


def main(argv):
    wanted = argv[1:] or list(EXPERIMENTS)
    unknown = [w for w in wanted if w not in EXPERIMENTS]
    if unknown:
        print(f"unknown: {unknown}; available: {list(EXPERIMENTS)}", file=sys.stderr)
        return 2
    failures = []
    for name in wanted:
        try:
            EXPERIMENTS[name]()
        except AssertionError as exc:
            failures.append((name, str(exc).splitlines()[0]))
            print(f"\n  [{name}] FALSIFIED: {exc}")
        except Exception as exc:
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"\n  [{name}] ERROR: {type(exc).__name__}: {exc}")
    print("\n" + "=" * 74)
    for eid, claim in RESULTS:
        print(f"  HOLDS      {eid:4s} {claim}")
    for eid, why in failures:
        print(f"  FALSIFIED  {eid:4s} {why}")
    print("=" * 74)
    print(f"  {len(RESULTS)}/{len(RESULTS) + len(failures)} claims hold\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
