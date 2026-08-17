"""Prototype of Seam A's `client.py` cache, from ../session-kv-decomposition.md.

NOT SHIPPING CODE. The design calls this "the risk Seam A introduces", because
`session_otel_bridge` resolves metadata once per span and a naive swap turns a
local file read into a network round trip inside span creation.

The four properties the design specifies, all exercised by run_experiments.py:

  write-through      a write populates the cache, so the first span of a
                     session is a hit rather than a guaranteed miss
  fail-open miss     a miss emits the span without session attributes and
                     schedules an out-of-band refresh; it never blocks or raises
  negative caching   an unknown id does not generate a request per span, and a
                     write for that id clears the negative entry
  thread_id refresh  an entry cached without thread_id is re-fetched once past a
                     short floor, because the server amends that field later

`transport` is injectable so tests need no live server -- also a design
requirement.
"""

import time
from collections import OrderedDict

POSITIVE_TTL = 60.0
NEGATIVE_TTL = 5.0
NO_THREAD_FLOOR = 2.0
MAX_ENTRIES = 1024


class SessionKVClient:
    def __init__(self, transport, clock=time.monotonic):
        self.transport = transport  # callable(session_id) -> dict | None ; may raise
        self.clock = clock
        self._cache = OrderedDict()  # sid -> (value_or_None, stored_at)
        self.stats = {"hits": 0, "misses": 0, "fetches": 0, "fail_open": 0, "refresh_queue": 0}

    # -- internals -------------------------------------------------------
    def _store(self, sid, value):
        self._cache[sid] = (value, self.clock())
        self._cache.move_to_end(sid)
        while len(self._cache) > MAX_ENTRIES:
            self._cache.popitem(last=False)

    def _fresh(self, sid):
        """Return (hit, value). Encodes the TTL rules, including thread_id."""
        entry = self._cache.get(sid)
        if entry is None:
            return False, None
        value, at = entry
        age = self.clock() - at
        if value is None:
            return (age < NEGATIVE_TTL), None
        if age >= POSITIVE_TTL:
            return False, None
        # one targeted invalidation: thread_id is amended out of process
        if not value.get("thread_id") and age >= NO_THREAD_FLOOR:
            return False, None
        return True, value

    # -- the write path (write-through) ----------------------------------
    def put_session_metadata(self, sid, metadata):
        self._store(sid, dict(metadata))  # populate on success: carries the first span

    # -- the read path (never blocks, never raises) ----------------------
    def session_metadata(self, sid):
        hit, value = self._fresh(sid)
        if hit:
            self.stats["hits"] += 1
            return value
        self.stats["misses"] += 1
        self.stats["refresh_queue"] += 1  # bounded queue; drops rather than blocks
        return None  # fail open: the span is emitted without session attributes

    def refresh(self, sid):
        """The out-of-band refresh a miss schedules. Errors are swallowed."""
        self.stats["fetches"] += 1
        try:
            value = self.transport(sid)
        except Exception:
            self.stats["fail_open"] += 1
            return None
        self._store(sid, value)  # None is cached too -- negative caching
        return value
