# Session KV decomposition — prototype

Executable backing for [`../session-kv-decomposition.md`](../session-kv-decomposition.md).

**This is not shipping code and is not wired into CI.** It exists to test the design's storage
mechanisms before anyone implements them in
[`agents/platform/scripts/session_kv_server.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/scripts/session_kv_server.py).
It earned its place immediately: **K2 falsified a constraint the design had stated as fact**, and
the correction changed how phase 1 has to be carried out.

## Running it

```bash
cd docs/designs/session-kv-decomposition
python3 run_experiments.py          # all ten, ~3s
python3 run_experiments.py K1 K7    # a subset
```

Standard library only — no cluster, no database server, no dependencies. Every experiment
**asserts**, so a non-zero exit means a claim in the design has stopped holding.

## What is here

| File                 | What it is                                                                                                                                                                          |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `session_kv.py`      | The proposed store: `txn()` with `BEGIN IMMEDIATE`, versioned schema with indices, 128-bit ids with idempotency replay, the outbox with step-wise recovery, one retention collector |
| `client.py`          | Seam A's caching client — write-through, fail-open, negative caching, the `thread_id` re-fetch                                                                                      |
| `run_experiments.py` | The experiments, with assertions                                                                                                                                                    |

The HTTP layer is deliberately absent. Every claim under test is about storage semantics, and
FastAPI is not installed in this environment anyway — which is also why `K9` models the threadpool
rather than reproducing it, and says so in its own output.

## What the experiments establish

| #     | Claim                                                            | Note                                                    |
| ----- | ---------------------------------------------------------------- | ------------------------------------------------------- |
| `K1`  | **R6** — a deferred read-modify-write loses a concurrent field   | And `txn()` keeps both                                  |
| `K2`  | **§2's journal-mode constraint**                                 | **FALSIFIED the design.** See below                     |
| `K3`  | **§4.1** — `locking_mode=EXCLUSIVE` locks every other opener out | Why exclusivity cannot precede the port                 |
| `K4`  | **R5** — the 7-day owner deletes rows the 14-day owner keeps     | Reproduced on one table                                 |
| `K5`  | **R9** — the retention predicate is a full table scan            | `EXPLAIN QUERY PLAN`: `SCAN` vs `SEARCH … USING INDEX`  |
| `K6`  | **R10** — a duplicate id is an `IntegrityError`, i.e. a 500      | And the idempotency key replays instead                 |
| `K7`  | **R2** — in-memory work is lost on restart; the outbox resumes   | Recovery lands on the in-flight step, not the beginning |
| `K8`  | Seam A's cache has all four specified properties                 | Write-through, fail-open, negative caching, `thread_id` |
| `K9`  | **R4** — a saturated sync pool starves the 2 s incident lookup   | **Modelled**, not reproduced                            |
| `K10` | Seam A's three migration cases                                   | Including the unstamped database a fresh-DB test misses |

### The one the design got wrong

§2 asserted that `journal_mode` "is a property of the file, not of the connection", that "the last
one to set it wins", and that disagreeing openers "will flip the file back and forth under each
other". Measured, none of that is symmetric:

|                                      | Behaviour                                                                         |
| ------------------------------------ | --------------------------------------------------------------------------------- |
| `WAL`                                | Persistent and file-level — set once, every later opener gets it                  |
| `TRUNCATE`, `DELETE`                 | **Per-connection**; after `WAL` then `TRUNCATE`, a third connection sees `delete` |
| Entering `WAL` with a peer connected | Succeeds, and sticks                                                              |
| Leaving `WAL` with a peer connected  | **Refused** — `database is locked`                                                |

So openers cannot oscillate: the dangerous direction just fails. The real constraint is
**sequencing, not agreement** — converting out of `WAL` needs a moment with no other opener, and
`store.py` holds its connection for the life of the gateway. Today's boot order supplies that
window by accident, which §2 now says explicitly instead of relying on it.

## What this does not cover

- **The HTTP layer** — trust zones, scopes, rate limits, path traversal (S6). Those need FastAPI
  and belong with the Seam B implementation.
- **Multi-writer behaviour on a real network filesystem.** `K2` shows WAL creates the `-shm`
  segment that R3 is about; it does not exercise NFS semantics, which needs the RWX volume.
- **Anything requiring a cluster** — leader-gating the watcher, the failover gap of §4.3, the
  end-to-end checks in §7.

## Where this goes

At **phase 1** the cases in `run_experiments.py` become tests beside the real modules, most
directly in `test_session_kv_server.py`. `K1`, `K2` and `K10` are the ones worth carrying over
first: the lost update, the journal-mode sequencing, and the unstamped-database migration are all
things no obvious test would otherwise catch. This directory should be **deleted** then — a
prototype kept alongside the implementation it seeded is just a second thing to keep in sync.
