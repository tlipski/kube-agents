# Session KV Decomposition

> **STATUS — draft; not implemented.** Nothing here ships today. `session_kv_server.py` is
> described in section 1 as it currently exists; sections 3 onward are the proposal.
>
> **Section 1 was re-verified against `main` on 2026-08-13, and four of its security findings had
> already been closed by work that landed in the meantime.** #616 authenticated every data route
> and pseudonymised chat identities; #641 added a per-severity daily alert cap; #405 grew the
> triage prompt. S1, S4, S5 and S8 are recorded below as closed, with what remains of each stated
> explicitly, and S2/S3 are re-scoped to the weaker claim that survives authentication. **The
> resilience findings were not touched: R1–R13 all still hold**, and R7 got worse in a way §4
> now records. Do not read section 1 as a to-do list — read section 9, which is the one that
> tracks what is left.
>
> **Re-verified again at `ebe33ba` on 2026-08-17, 42 commits later.** Every resilience finding still
> holds — WAL is still set by both openers, there are still no indices, session IDs are still 32
> bits, `hermes send` still has no timeout, and `init_db()` still runs at import. Line numbers
> moved a long way in `platformagent_manifests.go`, `docker-entrypoint.sh` and
> `platform_mcp_server.py`, and were re-derived by locating the cited text rather than by guessing
> offsets. Three things landed under this design and are folded in: the event watcher gained
> leading-edge debouncing and an emergency stop (§4.3), the companion supervisor design settled on
> a status file and an optional/required split that changes how R1 is closed (§4), and an
> in-cluster Postgres now exists, which §8 now declines on its merits rather than by inheritance.
>
> **Reviewed on 2026-08-17, and that re-derivation turns out to have covered only half the
> citations.** The reference links at the foot of this file were re-derived and are correct; the
> bare `L###` numbers written inline in section 1 were carried forward and are six lines low
> throughout — `session_kv_server.py` is 786 lines at `ebe33ba`, not 780, and `init_db()` begins at
> 221 rather than 215. All of them are corrected below. Two citations were wrong by more than an
> offset (`platform_mcp_server.py:555,601`, `store.py:13`) and are also fixed. The lesson for the
> next refresh is recorded in §0: **a bare `L###` is pinned to nothing**, so it survives a refresh
> that re-derives the links and stays wrong afterwards, which is worse than the stale anchor §0
> already warns about. Refresh the two together or not at all.

**Scope:** The `session_kv` mechanism — the SQLite store at
`/var/lib/kube-agents/session/session_kv.db`, the HTTP server on port 8699, and the six
components that read or write them.
**Owns:** the decomposition of `session_kv_server.py`, the trust boundary around port 8699,
retention policy ownership, and the leader-gated single-writer contract.
**Does not own:**

- The container's process model — what starts a long-lived process, restarts it, and reports its
  health. [`agent-process-supervisor.md`][agent-process-supervisor] owns that, and this design
  depends on it: the KV server becomes a supervised process there, and the readiness signal, the
  restart policy, and the lease timing guarantee at failover are all cited from it rather than
  restated here. **Its S1, S2 and S3 are all prerequisites for phase 3 below**, and its S4 _is_
  phase 3 seen from the other side — the same change, owned here — see the migration table.
- The Google Chat attribution path, which
  [`gchat-session-metadata-data-flow.md`](gchat-session-metadata-data-flow.md) documents. This
  design preserves it unchanged for chat sessions; see the bounded exception for `k8s-evt-*`
  sessions under [The OTel hot path](#the-otel-hot-path).

**Assumptions this design was written under** (confirmed with the requester):

1. The store becomes a single-owner service with a real authentication boundary.
2. Full decomposition, not hardening-in-place.
3. It must be correct at `replicas > 1`, reusing the Lease-based election already in
   `k8s-operator/internal/controller/leader_elect.py` rather than adding a new Deployment — and
   correct at `replicas: 1`, which is the default and where that election does not run today.
4. **`session_kv` is the only component that opens the database.** Every other component —
   `session_store`, `session_otel_bridge`, `session_manager`, the MCP servers, the watcher —
   reaches it through the API. This is a hard rule, not a preference; section 3 works out what
   it costs and section 6 orders the migration around it.
5. **Incident triage is a skill**, not a prompt string in the transport. `session_kv` invokes
   it; it does not author it.
6. **Session creation stays a first-class endpoint**, generalised: it takes an idempotency key
   and an optional initial prompt, so components other than the event watcher — the stockout
   investigator, the Pub/Sub adapter — can create agent sessions through it.

---

## 0. Source files

Every file this design cites, linked to `main` — follow these to read a file as it stands today.

Links **inside** the sections below work the other way: each names a line range and is anchored to
it, pinned to
[`ebe33ba`](https://github.com/gke-labs/kube-agents/commit/ebe33bafa4608f900348623cf8943fdeb15d701f), the commit these line numbers
were read from on 2026-08-17. Pinning is what keeps an anchor honest — `#L227` on a moving branch
comes to point at whatever later occupies that line, which is worse than no anchor at all. All the
URLs sit in one block at the end, so re-pinning after a refresh is a single edit.

Section 1 also writes bare `L###` numbers inline, and those are the ones to distrust. **A bare
number is pinned to nothing**: the 2026-08-17 refresh re-derived every anchored link and left every
inline number six lines low, and nothing in `make docs-check` can tell. They read against the same
commit as the links and must be refreshed in the same pass — if a future refresh cannot do both,
delete the inline numbers rather than leave them.

**One document this design depends on is not on `main` yet.**
[`agent-process-supervisor.md`][agent-process-supervisor] is PR #730, open at the time of writing.
An earlier revision linked it as `blob/main/docs/designs/agent-process-supervisor.md` in ten
places, all of which 404 — absolute links are not checked by anything, so a hard prerequisite went
missing in silence. It now resolves to the pull request through **one reference definition** at the
foot of this file, which is honest about what it is and swaps to a relative link in a single edit
when #730 merges. That swap is the point at which `make docs-check` starts guarding it; until
then, note that **phase 3 depends on a document that has not landed**.

**The subject**

| File                                                                                                                                                       | Its part in this design                                     |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| [`agents/platform/scripts/session_kv_server.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/scripts/session_kv_server.py)           | The 780-line file this design takes apart                   |
| [`agents/platform/scripts/test_session_kv_server.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/scripts/test_session_kv_server.py) | Its tests, including the route-coverage check Seam B reuses |

**The six consumers** (§1; the last three are the direct openers Seam A retires)

| File                                                                                                                                                                         | Access                                      |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| [`k8s-operator/cmd/k8s-event-watcher/`](https://github.com/gke-labs/kube-agents/tree/main/k8s-operator/cmd/k8s-event-watcher)                                                | HTTP — `injector.go`, `main.go`, `dedup.go` |
| [`agents/platform/scripts/platform_mcp_server.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/scripts/platform_mcp_server.py)                         | HTTP, plus the second launcher              |
| [`agents/platform/plugins/incident_context/__init__.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/plugins/incident_context/__init__.py)             | HTTP                                        |
| [`agents/platform/scripts/session_manager.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/scripts/session_manager.py)                                 | **direct `sqlite3`**                        |
| [`agents/chat/defaults/plugins/session_otel_bridge/bridge.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/chat/defaults/plugins/session_otel_bridge/bridge.py) | **direct `sqlite3`**, per span              |
| [`agents/chat/defaults/plugins/session_store/store.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/chat/defaults/plugins/session_store/store.py)               | **direct `sqlite3`**                        |

**Launch, deployment, and delivery**

| File                                                                                                                                                                                 | Its part in this design                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------- |
| [`deploy/shared/docker-entrypoint.sh`](https://github.com/gke-labs/kube-agents/blob/main/deploy/shared/docker-entrypoint.sh)                                                         | Step 5, and the `IS_BOOTSTRAP_PRIMARY` comment §4 rebuts |
| [`deploy/shared/start-services.sh`](https://github.com/gke-labs/kube-agents/blob/main/deploy/shared/start-services.sh)                                                               | Where the watcher actually runs, and its flags           |
| [`k8s-operator/internal/controller/platformagent_manifests.go`](https://github.com/gke-labs/kube-agents/blob/main/k8s-operator/internal/controller/platformagent_manifests.go)       | Env, mounts, storage mode, NetworkPolicy, RBAC           |
| [`charts/kube-agents/templates/platform-agent-secret.yaml`](https://github.com/gke-labs/kube-agents/blob/main/charts/kube-agents/templates/platform-agent-secret.yaml)               | The token generator Seam B extends                       |
| [`agentplugins/pubsub-platform/files/platforms/pubsub/adapter.py`](https://github.com/gke-labs/kube-agents/blob/main/agentplugins/pubsub-platform/files/platforms/pubsub/adapter.py) | The second hand-rolled copy of session creation          |

## 1. What exists today

[`agents/platform/scripts/session_kv_server.py`](https://github.com/gke-labs/kube-agents/blob/main/agents/platform/scripts/session_kv_server.py)
is 780 lines and a single FastAPI app — it was 447
when this design was first written, and the 333 lines added since went into two more
responsibilities rather than into separating the existing ones. It owns twelve:

| #   | Responsibility                     | Where                                                                                                                            |
| --- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Schema DDL                         | `init_db()`, L221-267 — and again in [`session_store/store.py:26`][store-py-26]                                                  |
| 2   | Retention GC                       | `cleanup_old_records()`, L274-285                                                                                                |
| 3   | Session-ID minting                 | `POST /sessions`, L294-307                                                                                                       |
| 4   | Session metadata reads             | L677-724                                                                                                                         |
| 5   | Incident report store              | L727-752 — a second, unrelated table                                                                                             |
| 6   | Alert text formatting              | `clean_workload_name` / `clean_reason_label` / `clean_event_message` / `get_severity_details`, L310-357                          |
| 7   | Chat delivery + thread-key parsing | `_post_initial_alert()`, L378-401                                                                                                |
| 8   | Session↔thread routing             | `_register_session_routing()`, L461-484                                                                                          |
| 9   | LLM prompt authoring               | `_build_agent_query()`, L507-561                                                                                                 |
| 10  | Gateway orchestration              | `_create_gateway_session` / `_start_agent_turn`, L487-577                                                                        |
| 11  | **Authentication**                 | `verify_api_key` and its helpers, L59-100 — added by #616                                                                        |
| 12  | **Alert rate limiting**            | `_alert_daily_limit` / `_claim_alert_quota` / `GET /v1/alert-quota`, L165-219, L404-458, L755-783 — a third table, added by #641 |

Responsibilities 11 and 12 are the argument for this design restated by events. Both are correct
and both were the right thing to ship; neither has anything to do with a key-value store, and
both landed in this file because there was nowhere else for them to go. A twelfth responsibility
is not a reason to renegotiate the decomposition — it is a reason to have one.

Six components depend on it, and only three go through the HTTP API:

| Consumer                                             | Path                         | Reads / writes                   |
| ---------------------------------------------------- | ---------------------------- | -------------------------------- |
| `k8s-event-watcher` (Go sidecar)                     | HTTP `127.0.0.1:8699`        | `POST /sessions`, `/inject`      |
| `platform_mcp_server.py:686,732`                     | HTTP `127.0.0.1:8699`        | metadata read, incident write    |
| [`incident_context/__init__.py:38`][__init__-py-38]  | HTTP `127.0.0.1:8699`        | incident read                    |
| [`session_manager.py:59`][session_manager-py-59]     | **direct `sqlite3.connect`** | metadata read                    |
| [`session_otel_bridge/bridge.py:136`][bridge-py-136] | **direct `sqlite3.connect`** | metadata read, **once per span** |
| [`session_store/store.py:178`][store-py-178]         | **direct `sqlite3.connect`** | metadata write, own retention    |

The HTTP API is therefore not a boundary. Half the consumers open the file directly, two of
them hand-roll the same `CREATE TABLE`, and two enforce different retention on the same rows.

Authentication makes that split sharper rather than softer. What #616 added is one dependency,
applied to every data route ([`session_kv_server.py:73-100`][session_kv_server-py-73-100]):

```python
def verify_api_key(
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
) -> None:
    expected = _expected_api_key()
    if not expected:
        logger.error(
            "%s is not set — refusing every authenticated request. ...",
            SESSION_KV_API_KEY_ENV,
        )
        raise HTTPException(status_code=503, detail="session KV authentication is not configured")

    presented = _presented_api_key(authorization, x_api_key)
    if not presented or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
```

It is correct as far as it goes — constant-time, fail-closed, and byte-compared for the
latin-1 header reason its own comment gives. What it cannot do is distinguish callers: there is
one `expected` value for the whole pod. And the three direct openers present nothing at all,
because a file has no opinion about who is reading it. So the boundary #616 built exists on
exactly the half of the traffic that was already going through the front door, and it answers
one question — inside the pod or not — rather than the four Seam B needs.

### 1.1 Security findings

Four of these — S1, S4, S5, S8 — were closed by #616 and #641 after the first draft. They are
kept in the table rather than deleted, because section 9 maps findings to phases and a silently
vanishing row is indistinguishable from a forgotten one. **Closed** rows state what shipped and
what, if anything, is left; the residue is what the phases below still have to carry.

| ID  | Severity   | Finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| --- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1  | **Closed** | ~~No authentication on any route, and the server binds `0.0.0.0`.~~ #616 added `verify_api_key` as a `Depends` on every data route (L73-100), and the entrypoint now binds `--host 127.0.0.1` ([`docker-entrypoint.sh:1190`][docker-entrypoint-sh-1190]). The original finding also mis-stated the network position: a NetworkPolicy **is** reconciled ([`platformagent_controller.go:532`][platformagent_controller-go-532], built at [`platformagent_manifests.go:3078`][platformagent_manifests-go-3078]), and its ingress allowlist is 8642/8643 plus the dashboard's 9119 (`:2986-3012`) — never 8699. What was deleted at [`platformagent_controller.go:496-504`][platformagent_controller-go-496-504] is a _legacy_ credential-isolation set from the two-Deployment era. **Residue:** one shared key for all callers, no scopes — which is what Seam B's zones are still for. |
| S2  | Medium ↓   | **Stored-report prompt injection.** `POST /v1/incidents` (L727) is now authenticated, so this is no longer reachable from outside the pod — but every container in the pod holds the same key, and `incident_context` still prepends the stored `report` verbatim to the user's next message in that thread ([`__init__.py:29-34`][__init__-py-29-34]), into an agent whose prompt says it is "explicitly authorized to create a … Pull Request." Downgraded from High: the caller must now be inside the pod.                                                                                                                                                                                                                                                                                                                                                                        |
| S3  | High       | **Event-data prompt injection.** `_build_agent_query` (L507) interpolates `message`, `name`, `namespace` and `cluster` — attacker-controllable by anyone who can create a pod or event in a watched cluster — into that same GitOps-authorizing prompt, unfenced. Authentication does not touch this one: the event text arrives through the front door, correctly authenticated, and is trusted as instructions once inside. **Still High, and the single largest open finding here.**                                                                                                                                                                                                                                                                                                                                                                                               |
| S4  | **Closed** | ~~Declared auth is theatre.~~ The watcher now runs with `--token-env=SESSION_KV_API_KEY` ([`deploy/shared/start-services.sh:139`][start-services-sh-139]) and the server verifies what it sends. The `cluster-internal-trusted` literal it used to send is still in the tree ([`platformagent_manifests.go:1410`][platformagent_manifests-go-1410], `:1976`) but belongs to `API_SERVER_KEY`/`AGENT_API_UPSTREAM_KEY`, a loopback sentinel rather than a secret — [`session_kv_server.py:47-53`][session_kv_server-py-47-53] says so in a comment explaining why it deliberately did not reuse it.                                                                                                                                                                                                                                                                                    |
| S5  | **Closed** | ~~PII disclosure.~~ `GET /v1/sessions` (L697) is authenticated, and #616 pseudonymised chat identities under `SESSION_KV_SALT`, with `_purge_plaintext_identities` (L109) stripping `user_email` from rows written before the change. **Residue:** the route still enumerates every session's routing metadata to any holder of the pod's one key, which is the argument for removing it in Seam B — now a tidying rather than a disclosure fix.                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| S6  | Medium     | **Path traversal into the gateway.** The `session_id` path param is unvalidated and interpolated into `f"{api_url}/api/sessions/{session_id}/chat"` (L568), reaching arbitrary gateway API routes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| S7  | Low        | **Metadata is trusted on read.** `send_notification` builds `target = f"{session_platform}:{chat_id}:{thread_id}"` from stored metadata ([`platform_mcp_server.py:698`][platform_mcp_server-py-698]). Anyone who can write metadata redirects the agent's reports to a chat space of their choosing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| S8  | **Closed** | ~~Free LLM turns.~~ `POST /sessions/{id}/inject` (L609) is authenticated, and #641 added a per-severity daily cap backed by the `alert_quota` table. **Residue:** the cap is deliberately fail-open (`_claim_alert_quota`, L412-414) and covers the alert path only, so it bounds cost rather than closing the primitive.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| S9  | Low        | Subprocess stdout/stderr from `hermes send` is logged on failure (L398); `_run_env()` passes the full environment, tokens included, to every subprocess.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| S10 | Low        | No size cap on `report` or `metadata`; no escaping of alert text before it reaches chat.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

S3 is worth reading in the original rather than in summary, because the two halves sit in one
string. `_build_agent_query` interpolates event fields at `:533-537`:

```python
    f"**Event Details:**\n"
    f"- **Resource:** {namespace}/{object_kind}/{object_name}\n"
    f"- **Event Reason:** {event_reason}\n"
    f"- **Warning Message:** {message}\n\n"
```

and grants authority 25 lines later, at `:558`, in the same returned value. That line is a single
unbroken f-string in the source; wrapped here for width, the sentence inside it reads:

> 1. A bare 'apply' (or 'apply recommended') means apply the option you marked '✅
>    **Recommended: Option \<letter\>**', or the only option you proposed if there was just one.
>    **You are explicitly authorized to create a new branch, modify the resource manifests in the
>    local checkout, commit, push, and open a GitHub Pull Request** matching the selected option.

`message` is the text of a Kubernetes event in a watched cluster. Anyone who can get a pod to
fail can choose it. Seam C's argument is entirely visible in the fact that those two blocks are
concatenated by the same `return`.

What the closures change about this design is narrower than it looks. They remove the argument
that the store is _unauthenticated_; they do not touch the argument that it is _undecomposed_.
Seam B's zones survive because one key shared by every container in the pod is not the same thing
as a caller identity, and S3 — the finding that authentication cannot reach, because the payload
is authentic and the _content_ is hostile — survives untouched. That is the finding Seam C exists
for, and it is now the most serious one open.

### 1.2 Resilience findings

**None of these were addressed by the work that closed S1/S4/S5/S8, and two — R7 and R8 — got
worse.** Verified against `main` on 2026-08-13 and again at `ebe33ba` on 2026-08-17.

| ID  | Severity | Finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | High     | **Unsupervised and unmonitored.** Started with `&` ([`docker-entrypoint.sh:1183-1190`][docker-entrypoint-sh-1183-1190]) and never restarted. The gateway container has **no readiness or liveness probe at all** ([`platformagent_manifests.go:2318-2340`][platformagent_manifests-go-2318-2340]), so `/healthz` is never called and a stopped KV server is invisible.                                                                                                                                                                                                                                                                                                                                                                  |
| R2  | High     | **At-most-once triage.** `BackgroundTasks` (L672) holds the whole flow in memory. A restart between `200 {"status":"injected"}` and the gateway call loses the incident silently — and the watcher, having got its 200, dedup-suppresses the retry for the dedup window, widened to 24h by #640.                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| R3  | High     | **Multi-writer SQLite on NFS.** At `replicas > 1` the operator moves the volume to RWX / `standard-rwx` ([`platformagent_manifests.go:101-113`][platformagent_manifests-go-101-113]). SQLite WAL requires shared memory and is unsupported on network filesystems. `journal_mode=WAL` is still set by both openers ([`session_kv_server.py:227`][session_kv_server-py-227], [`store.py:179`][store-py-179]). Every replica runs its own KV server, because entrypoint step 5 precedes `exec "$@"` and so never sees the election.                                                                                                                                                                                                       |
| R4  | Medium   | **Threadpool starvation.** Sync endpoints share AnyIO's 40-slot pool. `_start_agent_turn` holds a slot for up to 300 s (`urlopen(..., timeout=300.0)`, L573) and `hermes send` has **no timeout at all** (L381-387). One hang blocks `/v1/incidents/by-thread`, whose caller gives up at 2 s and fails open ([`incident_context/__init__.py:47`][__init__-py-47]) — users' replies silently lose context.                                                                                                                                                                                                                                                                                                                               |
| R5  | Medium   | **Two retention policies, one table.** The server deletes at 14 days (`SESSION_KV_CLEANUP_TTL_DAYS`, L45); `session_store` deletes at 7 (`SESSION_KV_RETENTION_DAYS`, [`store.py:117-122`][store-py-117-122]) on every write. The operator sets neither, so 7 silently wins over rows the server believes it keeps.                                                                                                                                                                                                                                                                                                                                                                                                                     |
| R6  | Medium   | **Lost updates.** `_register_session_routing` (L461) is a read-modify-write with no `BEGIN IMMEDIATE` — the `with conn:` around it is sqlite3's implicit _deferred_ transaction — racing `INSERT OR REPLACE` from [`store.py:156`][store-py-156] on the same `session_id`. #641 shows the fix is understood locally: `_claim_alert_quota` opens with `isolation_level=None` and issues a real `BEGIN IMMEDIATE` (L425-430), and says why in a comment. Nothing generalised it.                                                                                                                                                                                                                                                          |
| R7  | Medium ↑ | **Two launchers, one port** — and now two _different_ servers. [`platform_mcp_server.py:744-785`][platform_mcp_server-py-744-785] probes the port and spawns if free, a TOCTOU against the entrypoint's start. The loser exits with `EADDRINUSE` into a logfile nobody reads. Since #616 the two are no longer interchangeable: [`session_kv_server.py:116-125`][session_kv_server-py-116-125] records that the MCP-spawned fallback inherits the stdio MCP allowlist in `agents/platform/config.yaml`, which names `SESSION_KV_API_KEY` and **not** `SESSION_KV_SALT` — so which launcher wins decides whether identities can be hashed at all. A race that used to cost a duplicate log line now decides a data-correctness property. |
| R8  | Low ↑    | `init_db()` runs at import (L786); a failure makes the module unimportable and the server silently absent. It now creates three tables and runs `_purge_plaintext_identities`, a scan-and-write over `session_metadata` — so there is materially more that can fail at import than when this was written. `ALERT_DAILY_LIMITS` (L213-219) is likewise evaluated from the environment at import.                                                                                                                                                                                                                                                                                                                                         |
| R9  | Low      | No index on `updated_at` / `created_at` — `init_db()` creates none. Every GC `DELETE` and the `ORDER BY` in `list_sessions` is a full scan. GC only runs on writes, so a quiet system never collects and a busy one scans on every insert.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| R10 | Low      | `uuid4().hex[:8]` is 32 bits and the insert is a plain `INSERT` (L297-307) — a collision is a 500, not a retry.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| R11 | Low      | `_create_gateway_session` (L487) has no retry. The KV server is listening before `hermes gateway run` binds 8642, so alerts in the startup window are dropped with a log line.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| R12 | Low      | Schema drift: `incidents.created_at` in code (L243) vs `updated_at` in [`agents/platform/docs/session_management.md:136`][session_management-md-136] — which also prints a `SELECT … updated_at FROM incidents` troubleshooting command at `:183` that cannot run. That doc's Phase 1 also claims the proxy stores the triage report, which it does not — `send_notification` does, later.                                                                                                                                                                                                                                                                                                                                              |
| R13 | Low      | Unbounded logfile on the PVC; no metrics of any kind, while the watcher that feeds it is fully instrumented.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

---

## 2. Design constraints

- **`PYTHONPATH` is set pod-wide** ([`platformagent_manifests.go:1611`][platformagent_manifests-go-1611]), and
  `agents/platform/scripts/` is copied into the image. A package placed there is importable from
  the Chat Agent's plugins, the Platform Agent's plugins, and the MCP servers alike. This is what
  makes a single shared storage library possible without vendoring.
- **The election supervises a process, but not everywhere.** [`leader_elect.py:138`][leader_elect-py-138] starts
  `hermes gateway run` on acquire and terminates it on loss (L143-153); it labels the pod
  `kubeagents.io/is-leader=true` and the Service selects on that label
  ([`platformagent_manifests.go:2828`][platformagent_manifests-go-2828]). A second supervised process gets single-writer semantics and a
  leader-routed network path for free — **but only at `replicas > 1`**, because the operator makes
  the script the container's exec target only in that branch
  ([`platformagent_manifests.go:2279-2282`][platformagent_manifests-go-2279-2282]), and the script itself `execvp`s the gateway when the
  lease environment is absent ([`leader_elect.py:60-61`][leader_elect-py-60-61]). At the default single replica there is
  no supervisor to be supervised by.
  [`agent-process-supervisor.md`][agent-process-supervisor] closes that; this design assumes
  its S1 and S2 have shipped.
- **The pod's one key is not a set of caller identities.** #616 gives every container the same
  `SESSION_KV_API_KEY` from the same Secret, so authentication today answers "is this caller
  inside the pod" and nothing finer. Seam B's zones are the part that is still missing, and they
  have to be built on top of the shipped mechanism rather than beside it — see the token
  subsection there.
- **Failover blackholes traffic.** [`leader_elect.py:12-16`][leader_elect-py-12-16] says so explicitly: zero ready endpoints
  until the new leader labels itself. Anything crossing that window must be retried by the caller
  and deduplicated by the server. The supervisor design widens that window to buy a release-before-
  acquire guarantee for exclusively-held resources — which is what makes the file-lock handover in
  section 4 well-defined.
- **`journal_mode` is asymmetric, and an earlier draft of this bullet had it wrong.** Two openers
  set `WAL` today — [`session_kv_server.py:227`][session_kv_server-py-227] inside `init_db()`, and
  [`store.py:178-179`][store-py-178-179] on a long-lived connection:

  ```python
  conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
  conn.execute("PRAGMA journal_mode=WAL")
  ```

  This bullet used to say the mode is a property of the file, that the last setter wins, and that
  disagreeing openers "flip the file back and forth under each other". Measured (K2), SQLite does
  none of those symmetrically:

  |                                      | Behaviour                                                                                               |
  | ------------------------------------ | ------------------------------------------------------------------------------------------------------- |
  | `WAL`                                | **Persistent and file-level.** Set once, every later opener gets it                                     |
  | `TRUNCATE`, `DELETE`                 | **Per-connection.** They do not persist — after `WAL` then `TRUNCATE`, a third connection sees `delete` |
  | Entering `WAL` with a peer connected | **Succeeds**, and the file is `WAL` from then on                                                        |
  | Leaving `WAL` with a peer connected  | **Refused** — `OperationalError: database is locked`                                                    |

  So the openers cannot oscillate; the dangerous direction simply fails. That makes the real
  constraint a **sequencing** one rather than an agreement one: **converting the file out of `WAL`
  requires a moment when nothing else has it open.** Changing the constant in both files is not
  sufficient on its own — whichever process opens first has to do the conversion while it is
  alone, and `store.py` holds its connection for the life of the gateway.

  Today's boot order happens to give that window: the entrypoint starts the KV server before
  `exec`ing the gateway, so `init_db()` runs with no peer and can convert. Phase 1 therefore
  works, but it works **because of a start-order accident**, and it should say so rather than
  rely on it silently — the MCP launcher (R7) can win that race instead, and after phase 3 the
  supervisor owns the ordering explicitly.

  The accident is also not permanent, which is why §4.1 requires the conversion to be survivable
  rather than merely sequenced: from phase 3 the supervisor can restart the KV server on its own
  while the gateway is still holding `store.py`'s connection, and that restart opens into a peer
  by construction. A conversion that must succeed would make the supervisor's own restart policy
  the thing that breaks it.

- The Google Chat attribution contract in `gchat-session-metadata-data-flow.md` — the fixed
  metadata allowlist and the span attribute set — is unchanged by this design for chat sessions.

---

## 3. The four seams

The decomposition cuts along the boundaries the current file crosses.

```
agents/platform/scripts/session_kv/
  client.py        # SEAM A: the ONLY thing other components import
  errors.py        # typed failures the client raises
  server/          # nothing outside this package imports from server/
    db.py          #   connection factory, busy_timeout, txn() helper — the only module with SQL
    schema.py      #   DDL + versioned migrations, single owner
    sessions.py    #   SessionStore: metadata read/write, routing updates
    incidents.py   #   IncidentStore: triage reports
    quota.py       #   AlertQuota: the per-severity daily cap added by #641
    outbox.py      #   durable work queue + its in-process drainer (see Seam D)
    retention.py   #   one collector, one window per store
    render.py      #   SEAM C: severity, name, reason, message formatting
    notify.py      #   SEAM D: Notifier protocol + google_chat / slack adapters
    triage.py      #   SEAM D: alert -> chat -> routing -> invoke the incident-triage skill
    api/
      app.py       #   SEAM B: app factory
      auth.py      #   bearer verification, caller identities
      routes_*.py  #   sessions / incidents / alerts, one file each
```

The triage prompt is deliberately absent from this tree — it lives in
`agents/platform/skills/incident-triage/SKILL.md`. See Seam C.

### Seam A — one process opens the file

**`session_kv` is the only component that may touch SQLite. Everything else goes through the
HTTP API.** No shared SQL library, no second importer of `db.py` — the `server/` subpackage
exists precisely so that "does this component open the database?" is answerable by grepping for
the import.

That retires all three direct openers:

| Component                                            | Today                      | After                                           |
| ---------------------------------------------------- | -------------------------- | ----------------------------------------------- |
| [`session_store/store.py:178`][store-py-178]         | `sqlite3.connect` + DDL    | `client.put_session_metadata()`                 |
| [`session_otel_bridge/bridge.py:136`][bridge-py-136] | `sqlite3.connect` per span | `client.session_metadata()`, cached — see below |
| [`session_manager.py:59`][session_manager-py-59]     | `sqlite3.connect`          | `client.session_metadata()`                     |

`client.py` ships alongside the other scripts and is importable from the Chat Agent's plugins,
the Platform Agent's plugins, and the MCP servers alike ([`platformagent_manifests.go:1611`][platformagent_manifests-go-1611]).
It carries the caller's token, a short timeout, typed errors, and an injectable transport so
tests do not need a live server. Being the single client, it is also the single place that
implements retry, caching, and fail-open policy — today each of the five callers improvises its
own, with timeouts of 2 s, 3 s, and none.

The token-reading half of that is already duplicated three ways and is the cheapest part to
collapse: [`platform_mcp_server.py:30-41`][platform_mcp_server-py-30-41] has a `_session_kv_headers()` helper,
[`incident_context/__init__.py:40-45`][__init__-py-40-45] builds the same header inline with its own fail-open
comment, and the Go watcher reads the same variable through `--token-env`. `client.py` should
absorb the two Python copies on the way past.

`schema.py` gains a `schema_version` table with explicit migrations, indices on the timestamp
columns, and a `txn()` helper issuing `BEGIN IMMEDIATE`. With `schema.py`
as the only DDL, the duplicated `CREATE TABLE` — responsibility #1 of section 1, hand-rolled a
second time at [`store.py:24-30`][store-py-24-30] — stops existing. (R12 is a different problem, code-versus-docs
drift, and it is fixed in section 7 by updating the doc.)

`txn()` is necessary for R6 and, on its own, **not sufficient** — an earlier draft of this section
said `BEGIN IMMEDIATE` was the fix and that was wrong. The two writers are worth showing side by
side, because they are not the same shape. `_register_session_routing` (`:461-484`) reads, mutates
in Python, and writes back under sqlite3's implicit **deferred** transaction:

```python
with closing(sqlite3.connect(SESSION_KV_DB_PATH, timeout=5.0)) as conn:
    with conn:
        row = conn.execute(
            "SELECT metadata FROM session_metadata WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row:
            meta = json.loads(row[0])
            meta["thread_id"] = thread_id
            ...
            conn.execute(
                "UPDATE session_metadata SET metadata = ? WHERE session_id = ?",
                (json.dumps(meta), session_id),
            )
```

`_claim_alert_quota` (`:425-430`), added later by #641, does it correctly and explains why:

```python
# isolation_level=None hands transaction control to us so the BEGIN
# IMMEDIATE below is the real thing rather than sqlite3's implicit
# deferred transaction.
with closing(sqlite3.connect(SESSION_KV_DB_PATH, timeout=5.0, isolation_level=None)) as conn:
    # IMMEDIATE takes the write lock before the read. A deferred
    # transaction would let two alerts arriving together both read
    # `sent` at limit-1 and both conclude they are within budget, which
    # is the one bug a cap must not have.
    conn.execute("BEGIN IMMEDIATE")
```

The knowledge is in the file; what is missing is a shared helper that makes it the default. That
is all `txn()` is — the second block, hoisted into `db.py`, with the first block as its first
caller.

#### Serialising the two writers does not stop one of them clobbering the other

The other side of R6 is not a read-modify-write at all.
[`SessionMetadataStore.write`][store-py-148-171] is a **blind full-blob `INSERT OR REPLACE`**: it
serialises whatever dict `SessionMetadata.from_event` produced and replaces the row, never reading
what is there. So when it follows `_register_session_routing`, the `thread_id` and `chat_id` that
routing just added are gone — deterministically, whatever the isolation level. `BEGIN IMMEDIATE`
makes the two atomic with respect to each other; it does not make a full-row replace into a merge.

This matters more than the race it was mistaken for, because the ordering is the common one rather
than the unlucky one: `_register_session_routing` writes `thread_id` at step 2 of
`trigger_agent_troubleshooter`, and `log_event_to_db` fires on every inbound gateway message in
that thread thereafter ([`store.py:212`][store-py-212]).

It also means the prototype's K1 confirmed the wrong shape — it modelled two read-modify-writes,
which is the case `txn()` does fix — and that §7's test "concurrent `register_routing` and
`store.write` on one session leave both fields present" would have failed against the fix this
section used to propose. Both are corrected below.

**The fix is to stop keeping routing in the blob.** `thread_id`, `chat_id` and `platform` become
real columns on `session_metadata`; the JSON blob keeps only the attribution payload
`SessionMetadata` owns. Then the two writers stop overlapping at all — the metadata write owns the
blob, the routing update owns three columns, and neither can express the other's data, so there is
nothing left to clobber. Four things follow, and they are why this is the cheaper answer rather
than merely the correct one:

- **R6 stops being a concurrency property**, so it cannot regress under a future writer that
  forgets `txn()`. Structure beats discipline here.
- **S7's shape check gets somewhere to live.** Validating `chat_id`/`thread_id` on the way out of
  an opaque blob means validating whatever `json.loads` returned; validating a typed column is a
  constraint at the write, which is where a check belongs.
- **The OTel bridge caches a narrow projection** — three columns and a session id — rather than a
  document whose size nothing bounds (S10).
- **`PATCH /v1/sessions/{id}/routing` becomes a plain `UPDATE`** of named columns, so the endpoint
  the zone table already lists stops being a read-modify-write on the server side too.

The migration is the v1 → v2 step in `schema.py`: add the columns, backfill them from the existing
blob, and leave the blob's copies in place for one release so a rollback still reads. `txn()` stays
required regardless — `_register_session_routing` is unsafe by construction and so is any later
read-modify-write — it is just no longer load-bearing for this particular pair.

**Migrations have to adopt a database nobody stamped.** Every live PVC already carries a
`session_metadata` table created by `CREATE TABLE IF NOT EXISTS` in one of two places, and no
`schema_version` row anywhere. The runner therefore needs three cases, not two: no tables → create
at head; tables but no `schema_version` → stamp as v1 and continue; otherwise → migrate forward.
The middle case is the one a fresh-database test will not cover, so it gets a fixture built by the
old `store.py` DDL.

The three tables stay logically separate stores with separate owners and separate retention —
`SessionStore` belongs to chat ingress, `IncidentStore` to the triage flow, `AlertQuota` to
delivery policy. They share a file only as an implementation detail below the API.

`AlertQuota` is the newest and the easiest to misfile. It is not session state and not an
incident record; it is a counter that exists to survive a restart, which its own comment in
`init_db()` (L248-255) explains. Two properties have to survive the move intact, because both are
deliberate: the claim is a real `BEGIN IMMEDIATE` transaction (L425-430), and the whole path
fails **open** — a database that cannot be written must not withhold an alert from an on-call
human. `quota.py` inherits both, and `txn()` from Seam A is what the first one becomes.

**One import-graph change comes with this.** `session_kv_server.py` imports `agent_common_server`
for `_run_env` and the config paths, and `agent_common_server.py:15,24` imports `SessionManager`
and constructs one at module scope. Once `SessionManager` is a `client.py` consumer, the server
process builds an HTTP client to itself at import time. Move `_run_env` and the path constants
into a leaf module that imports nothing from this tree, and have `session_kv/server/` depend on
that instead — which also keeps the boundary check in section 7 from having to special-case the
server's own transitive imports.

#### The OTel hot path

`session_otel_bridge` is the one consumer where this constraint is not free. It resolves
metadata **once per span** — `start_span` ([`bridge.py:56`][bridge-py-56]) reaches
`_span_attributes_for_session` (`:93`) and then `_metadata_for_session` (`:130`), which opens the
database — so a naive swap turns a local file read into a network round trip inside span creation — strictly worse than what it replaces, and on a path
that must never block or throw.

The client therefore has to make the bridge's usage a cache hit almost always:

- **The cache is write-through, and that is what carries the first span.** A read-through cache
  alone would be a regression, not a port: `log_event_to_db` is a `pre_gateway_dispatch` hook, so
  today the metadata is in SQLite _before_ the gateway dispatches and the very first span of a
  session already resolves. Under a read-through cache that span is a guaranteed miss — and it is
  the root span of the turn. So `client.put_session_metadata()` populates the cache on success.
  Writer and reader are the same process, and the write happens first, so the hit is structural
  rather than lucky.
- **The cache is process-wide, and that is a requirement rather than an implementation detail.**
  The writer and the reader are the same process but not the same module: `session_store` and
  `session_otel_bridge` are two independently loaded Hermes plugins. If each constructs its own
  client, each gets its own cache, the bridge never sees the store's write, and the write-through
  property above quietly evaporates — leaving exactly the read-through regression it exists to
  prevent. `client.py` therefore exposes a module-level singleton and the plugins take it; a
  per-instance cache is the one shape that must not be allowed. §7 tests this directly, because a
  broken singleton fails no assertion that is only about correctness.
- A TTL cache keyed by `session_id`, bounded (LRU, order 1024 entries) so that a long-lived
  gateway cannot accumulate one entry per session seen. Session metadata is near-immutable —
  written once at session creation, amended once when `thread_id` is resolved — so a 60 s TTL is
  generous.
- **Miss is fail-open, not fetch-inline.** On a miss the bridge emits the span without the
  session attributes and schedules an out-of-band refresh on a bounded queue that drops rather
  than blocks, so the next span is enriched. Adding request latency to span creation is not an
  option; neither is raising out of it. **This is also what bounds miss traffic**, so there is no
  negative cache: a drop-don't-block queue with one in-flight refresh per `session_id` already
  makes "a request per span for an unknown ID" impossible, and an earlier draft's second TTL, its
  own invalidation rule and its own tests bought nothing the queue was not already buying. One
  mechanism, one failure mode.
- **One targeted invalidation.** `thread_id` is the single field amended out of process: the
  server writes it for `k8s-evt-*` sessions when the initial chat post resolves a thread. A
  cached entry that has no `thread_id` is therefore re-fetched once past a short floor, rather
  than being trusted for the full TTL. This converges — the entry either gains the field or the
  session never had one — and it is what makes the section 7 test ("a `thread_id` written after a
  read is visible within the TTL") satisfiable at all.

The residue, stated rather than waved at: for `k8s-evt-*` sessions the early spans may carry
`session.id` without `chat.thread_id`, because that field genuinely does not exist yet at the
time they are emitted. That is the bounded exception to the "attribution unchanged" claim in the
header. Chat sessions, which is what
[`gchat-session-metadata-data-flow.md`](gchat-session-metadata-data-flow.md) is about, are
unaffected.

`session_store`'s write hook has the same shape of risk on a colder path: `log_event_to_db`
([`store.py:212`][store-py-212]) runs on every inbound gateway message, so the client write must be bounded and
fail-open exactly as the current implementation already is.

#### What this costs

Today a stopped KV server degrades gracefully: `session_store` keeps persisting and OTel
attribution keeps working, because both bypass it. Under an API-only rule they stop — the blast
radius of the server being down grows from "no incident triage" to "no session persistence and
no attribution either."

That is an acceptable trade only if the server stops being the unsupervised background job it is
today. **The supervision, probe, and leader-ownership work is a prerequisite for this seam, not a
parallel workstream** — which is why section 6 puts phase 3 ahead of the port, and why S1 and S2
of [`agent-process-supervisor.md`][agent-process-supervisor] come before that. It is
also why the cache above has to be write-through: a fail-open miss is an acceptable answer to the
server being down, but it must not be the normal path.

### Seam B — API and trust zones

Today every route sits in one undifferentiated zone — #616 authenticated them all with **one key
and no scopes**, so the boundary distinguishes inside-the-pod from outside-the-pod and nothing
else. Split by what a caller can cause:

| Zone | Routes                                                                                                         | Credential                    |
| ---- | -------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| 0    | `GET /healthz`                                                                                                 | none; no data in the response |
| 1    | `GET /v1/sessions/{id}/metadata`, `GET /v1/incidents/by-thread`, `GET /v1/alert-quota`, `GET /v1/outbox/stats` | `agent`                       |
| 2    | `PUT /v1/sessions/{id}/metadata`, `POST /v1/incidents`                                                         | `agent`                       |
| 3    | `POST /v1/sessions`, `POST /v1/sessions/{id}/messages`, `POST /v1/alerts`, `PATCH /v1/sessions/{id}/routing`   | `ingest`                      |

**`PUT /v1/sessions/{id}/metadata` is the route Seam A's port depends on, and an earlier draft of
this table did not have it.** `session_store` is the busiest consumer of the new API — a write on
every inbound gateway message — and phase 5 cannot move it without somewhere to write. It carries
the attribution blob only: the routing columns are not settable through it, which is what keeps
the clobber of Seam A from being reintroduced as an API shape. It is `PUT` rather than `PATCH`
because the blob genuinely is replace-semantics; it is the routing fields that were never
replace-semantics, and they are no longer in it.

**Two credentials, not one per zone.** The zones say what a route can cause; the credentials say
who may cause it, and the callers only cluster two ways. `ingest` is the event watcher and nothing
else. `agent` is everything living in or below the gateway process — `session_store`,
`session_otel_bridge`, `session_manager`, `incident_context`, the MCP servers — which share a
process tree and therefore share a blast radius no token split can separate. Minting a third and
fourth key to divide zone 1 from zone 2 would be dividing a caller from itself: two more Secret
keys, two more rotation paths, and no attacker stopped. Keep the zones as route labels, because
that is what the coverage test asserts against and what makes the ingest split reviewable; mint
two identities.

`GET /healthz` is already exempt in the shipped code and is already tested as exempt
([`test_session_kv_server.py:238`][test_session_kv_server-py-238]), so zone 0 is a description rather than a change. #616 also
left behind the check this table most needs:
`test_declared_routes_are_all_covered` (`:224`) walks the app's route table and asserts every
route carries the dependency. Generalise it to assert every route carries a **zone**, and the
table above stops being prose the first time someone adds a route.

Zone 3 is everything that can cause a chat post, a model turn, or a change to where either is
delivered, so it gets its own credential and its own rate limit — #641's daily cap is the rate
limit for the alert path and predates the zone, so it moves under zone 3 rather than being
reinvented. Removing `GET /v1/sessions` (the
list route) finishes S5; no consumer uses it — but it is a published route in
`gchat-session-metadata-data-flow.md:113`, so removing it is a change to a documented API and
lands in section 7's docs list rather than passing silently.

**Routing fields are validated on the way out, not trusted because they were stored** (S7).
`send_notification` builds `target = f"{session_platform}:{chat_id}:{thread_id}"` from whatever
the store returns ([`platform_mcp_server.py:698`][platform_mcp_server-py-698]), which turns metadata-write access into control
of where the agent's reports go. Two changes: `PATCH /v1/sessions/{id}/routing` sits in zone 3
behind the `ingest` credential rather than alongside the ordinary metadata write — which is why
the zone table above lists it there and not in zone 2 — and `chat_id`/`thread_id` are shape-checked
against the platform's format. With Seam A's routing columns the check happens on the way **in**,
at the write, rather than on the way out at every read: a typed column can carry a constraint, and
a field validated once at the boundary cannot be read back malformed by a caller who forgot to
check.

#### Session creation is a general primitive

`POST /sessions` stays — generalised, not deleted. Today it mints an opaque ID and nothing
else, which is why the watcher needs a second `/inject` call to say what the session is _for_.
Folding the prompt into creation makes it useful to callers that are not the event watcher:

```
POST /v1/sessions
{
  "idempotency_key": "<caller's dedup key>",   # required
  "prompt":          "<initial turn, optional>",
  "skill":           "incident-triage",         # optional; invoked with the prompt
  "profile":         "platform",                # optional; defaults to platform
  "metadata":        { … }                      # optional routing/attribution seed
}
→ 201 { "session_id": "k8s-evt-…", "status": "created" }
→ 200 { "session_id": "k8s-evt-…", "status": "duplicate" }
```

The idempotency key is stored under a unique index and replayed rather than re-executed, which
is what makes a caller's retry safe across leader failover (R2, R11). Session IDs move to 128
bits (R10).

**The server still mints the ID**, so no caller supplies one in a path — S6 stays closed.
Follow-up turns go to `POST /v1/sessions/{id}/messages`, where the ID is resolved against the
store before use; an ID that is not in the store is a 404, never a string interpolated into a
gateway URL. That is a stronger guarantee than pattern-matching the parameter, and it is why the
endpoint shape can safely come back.

This directly serves callers beyond the watcher. The stockout investigator gets a supported way
to open an agent session. So does the Pub/Sub adapter, which today hand-rolls exactly this
against the gateway — `_run_turn_via_api` at [`agentplugins/pubsub-platform/files/platforms/pubsub/adapter.py:988`][adapter-py-988]
creates a session, works around the API's 409-and-duplicate-title quirks, then posts a prompt,
under a comment at `:991` reading "Mirrors what `agents/platform/scripts/session_kv_server.py`
does for event-watcher alerts." Two hand-rolled copies of one flow, each with its own idempotency
workaround, is the argument for making it a primitive.

`POST /v1/alerts` remains, but shrinks to an alert-shaped facade over the primitive: it renders
the event (Seam C), posts the initial chat message, then calls session creation with the
`incident-triage` skill. The watcher stays out of the business of emoji and PDB message
cleanup.

**Extend the shipped minting mechanism; do not build a second one.** An earlier draft of this
section proposed that the operator reconcile its own `<agent>-session-kv-tokens` Secret, on the
reasoning that `platform-agent-secrets` is referenced by `SecretKeyRef`
([`platformagent_manifests.go:53`][platformagent_manifests-go-53], `:1445-1452`) and therefore created outside the operator — so
adding required keys to it would fail every existing installation on upgrade with
`CreateContainerConfigError`.

**That problem is real, and #616 already solved it — differently.** It added two required keys,
`SESSION_KV_API_KEY` and `SESSION_KV_SALT`, and made them safe in the Helm chart rather than in
the operator
([`platform-agent-secret.yaml:6-25`][platform-agent-secret-yaml-6-25]):

```gotemplate
{{- /* Two pod-scoped values the operator injects but no operator asks for: the
     Session KV bearer token and the identity-hashing salt. Both are generated
     here when absent, and both must survive `helm upgrade` — a new salt breaks
     correlation between a user's old and new sessions, and a new token 401s the
     event watcher until every container holding the old one restarts. */}}
{{- $supplied := .Values.platformAgent.credentials.data | default dict }}
{{- $existing := (lookup "v1" "Secret" .Release.Namespace .Values.platformAgent.credentials.secretName) }}
{{- $existingData := (get ($existing | default dict) "data") | default dict }}
{{- $generated := dict }}
{{- range $key := (list "SESSION_KV_API_KEY" "SESSION_KV_SALT") }}
  {{- if not (hasKey $supplied $key) }}
    {{- if hasKey $existingData $key }}
      {{- $_ := set $generated $key (get $existingData $key | b64dec) }}
    {{- else }}
      {{- $_ := set $generated $key (randAlphaNum 48) }}
    {{- end }}
  {{- end }}
{{- end }}
```

So the two caller tokens are **one further key in the same Secret through the same template**,
not a new object. `SESSION_KV_API_KEY` is retained as the `agent` identity — it is already what
every gateway-side caller holds — and `SESSION_KV_INGEST_KEY` is added for the watcher, which is
the only caller that has to be separated from the rest:

- The generation loop is already a `range` over a key list; adding identities is adding names to
  that list.
- Carry-forward, upgrade safety, and the "supplied wins over generated" precedence come for free
  and are already the behaviour operators have been upgraded through once.
- Rotation stays what it is today — delete the key and let the template regenerate it — and the
  config hash in `buildDeployment` restarts the pod that consumes it.
- `SESSION_KV_API_KEY` keeps working as a general-purpose key accepted on every zone for one
  release, and narrows to the `agent` identity after it, which is what lets the watcher and the
  plugins move independently rather than in lockstep.
- Reusing the shipped key as one of the two identities means the migration adds exactly one
  secret, and the caller that has to change is the one caller — the watcher — whose credential is
  already passed by name through `--token-env`.

One caveat this inherits rather than introduces: `lookup` returns empty under `helm template` and
`--dry-run`, so a rendered-only manifest shows fresh values. The template already says so; a
GitOps flow that diffs rendered output will see spurious changes on every render, and that is a
property to know about before adding another generated key to it.

A caller identity whose token is empty is rejected on zones 1–3 — fail closed, matching the 503
`verify_api_key` already returns for an unset key — but the server logs the missing identity
loudly at startup and keeps zone 0 open, so the failure is diagnosable from the pod's own logs
rather than from a wall of 401s.

`SessionManager.verify_delegation_headers` ([`session_manager.py:208`][session_manager-py-208]) is already an implemented
and tested HMAC verifier with timestamp-skew checking — agent-side callers should reuse it rather
than grow a second scheme; the Go watcher gets a plain bearer token, since it would otherwise
have to reimplement the canonicalization.

**The loopback bind is already done.** An earlier draft proposed changing the bind from `0.0.0.0`
to `127.0.0.1` as this design's answer to S1; #616 shipped it ([`docker-entrypoint.sh:1190`][docker-entrypoint-sh-1190]), with
the same reasoning this section gave — every caller is inside the pod's network namespace, and
the port carries chat identifiers. Nothing here needs to move it, and the NetworkPolicy it was
weighed against turns out to have never allowed 8699 in the first place (S1). What remains true
is the part that mattered: co-location is not a boundary. The pod's other containers still reach
`127.0.0.1:8699` and still hold the same key, which is why the zones above are the work that is
actually left.

### Seam C — rendering, and triage as a skill

`render.py` takes the four pure formatting functions unchanged; they are already the
best-tested part of the file ([`test_session_kv_server.py:27-76`][test_session_kv_server-py-27-76]) and only need moving.

**The triage prompt does not move to a data file owned by `session_kv`. It becomes a skill.**
`_build_agent_query` (L507-561) is a single Python string that encodes the analysis instruction,
the report format, the console links, the human call-to-action, the follow-up GitOps procedure,
and the authorization to execute it. None of that is transport concern; all of it is agent
capability, and this repository already has a mechanism for agent capability.

**It has grown since this design was written, which is the argument rather than a footnote.** It
was 37 lines; #405 added the "propose as many options as the root cause warrants, mark exactly
one Recommended" block and it is now 56. That change is prompt engineering — it tunes how the
model writes a report — and it shipped as a diff to a FastAPI transport module, where the
repository's `review-skill-quality` skill cannot see it and no skill-authoring review applies.
The next such change will go to the same place for the same reason.

So: `agents/platform/skills/incident-triage/SKILL.md`, alongside the 36 skills already there. It
belongs to the Platform Agent under the placement rule in `AGENTS.md` — it performs GitOps
writes, so it is not a Cluster Agent read-only debugging skill. `session_kv` stops authoring
prompts entirely and instead invokes the skill with structured event data, via the `skill` field
on session creation.

Four things follow.

1. **Authority stops travelling with the payload.** Today the sentence "you are explicitly
   authorized to create a branch … and open a Pull Request" arrives in the same string as
   attacker-influenceable event text. As a skill, the authorization is a reviewed file in the
   image; the message merely names the skill. Authority becomes a property of the agent, never
   of the message that reaches it — which is the actual fix for S3, and a stronger one than
   fencing alone.
2. **Untrusted event fields are still fenced and labelled** as data rather than instructions,
   with control characters stripped and length capped. The skill states that its event input is
   untrusted. `incident_context` gets the same treatment for the stored report it prepends
   (S2, S10).
3. **The report format becomes editable without redeploying a Python service** — and reviewable
   by the repository's existing `review-skill-quality` skill, which a string literal inside a
   FastAPI app is not. #405 is the worked example: a pure prompt change that had to ship as a
   service diff.
4. **Other entry points can reuse it.** A kanban card can name `incident-triage` the same way
   the Pub/Sub adapter already passes `--skill` when filing work ([`adapter.py:958`][adapter-py-958], with the
   rationale at `:936`), and the stockout investigator can invoke it directly. The skill becomes
   the single definition of what triage means, rather than one definition per caller.

### Seam D — delivery and orchestration

`notify.py` defines a `Notifier` protocol with `google_chat` and `slack` adapters, absorbing the
platform-specific thread-key derivation currently inline at L392-396. It **inherits** the missing
subprocess timeout rather than adding it: R4's timeout is a one-line change to a live hang on the
default install, so it lands in phase 1 where the split happens and is simply carried into
`notify.py` here. Waiting until phase 7 to bound a `subprocess.run` that today has no timeout at
all would be holding a fix hostage to a refactor. It is also where S9 is closed: `hermes send` is invoked with an explicit
environment allowlist rather than `_run_env()`'s copy of everything the pod has, and a failure
logs the exit status with a truncated, redacted `stderr` instead of the raw streams.

`triage.py` holds the sequence, and `BackgroundTasks` is replaced by a durable outbox:

```sql
CREATE TABLE outbox (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  payload         TEXT NOT NULL,
  step            TEXT NOT NULL DEFAULT 'post_alert',
  step_result     TEXT,
  state           TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | failed
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 8,
  last_error      TEXT,
  next_attempt_at TIMESTAMP NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX outbox_due ON outbox (state, next_attempt_at);
```

`POST /v1/alerts` commits a row and returns. This converts the flow from at-most-once to
at-least-once across restarts (R2).

**The drainer runs inside the API process, not beside it.** Seam A's rule is that one process
opens the file, and section 4's journal mode enforces it with `locking_mode=EXCLUSIVE`; a
separate worker process would be a second opener and could not open it at all. So the drainer is
a task started on app startup and stopped on shutdown, and the supervisor has one KV process rather
than two. It gets **its own executor**, not the shared 40-slot AnyIO threadpool — otherwise R4
returns through the worker instead of through `_start_agent_turn`, which is the failure the
outbox exists to prevent. The bound on that executor is what stops an alert burst from starving
the read paths.

**At-least-once needs step-wise recovery, because `hermes send` is not idempotent.** The
idempotency key makes the _caller's_ retry safe; it does nothing about the worker exiting between
posting to chat and recording that it did. So the row advances through named steps —
`post_alert → register_routing → create_session → start_turn` — each committing its result before
the next begins, and recovery resumes at `step` rather than at the beginning. Only the step that
was in flight is ambiguous.

For the one step where ambiguity is expensive, the policy is explicit: **a `post_alert` that may
have landed is not retried.** A duplicate alert in a chat space is worse than a missing thread
binding, and the flow degrades gracefully without one — the triage turn still runs, and
`send_notification` already falls back to the platform's default target when it cannot resolve a
thread ([`platform_mcp_server.py:700-712`][platform_mcp_server-py-700-712]). The row is marked with `last_error` so the ambiguity is
visible rather than inferred.

**That special case is the whole reason the step machine exists, and it may be removable.** Google
Chat's `spaces.messages.create` takes a client-supplied `messageId` and is idempotent on it, so if
`hermes send` can be given one — passed down as a flag, derived from the outbox row's idempotency
key — then `post_alert` becomes as retryable as every other step. At that point the ambiguous-step
policy above goes, and with it most of the argument for tracking `step`/`step_result` at all: a
flow whose every step is idempotent can be retried from the beginning, which is a loop rather than
a state machine. **Check this before building the step machine, not after.** The recovery design
here is correct for a non-idempotent `hermes send`; it is a lot of machinery to carry if the
premise turns out to be false, and the premise is one flag away from being checkable.

Rows that exhaust `max_attempts` become `state = 'failed'` with their last error, and
`GET /v1/outbox/stats` (zone 1) reports the state and attempt distribution. That endpoint is not
a convenience: it is the only way to inspect the queue once the database is opened exclusively,
and section 7's end-to-end check uses it in place of the `sqlite3` shell it would otherwise
need.

---

## 4. Leader ownership

```mermaid
sequenceDiagram
    participant Lease as coordination.k8s.io Lease
    participant SV as supervisor (leader_elect.py)
    participant KV as session_kv API + outbox drainer
    participant W as event-watcher (sidecar, all replicas)

    SV->>Lease: acquire
    SV->>SV: label pod is-leader=true
    SV->>KV: start (one process, not two)
    KV->>KV: open DB with retry until the outgoing leader releases
    W->>Lease: watch; holder == $HOSTNAME ?
    Note over W: followers idle here — block, never exit
    W->>KV: POST /v1/alerts (127.0.0.1, bearer)
    SV->>Lease: renew every 5s
    Note over SV: on loss: stop processes, drop label
    Note over W: on loss: stop watching, resume idling
```

Concretely:

- The supervisor starts the KV server as a second process on acquire and stops it on loss, exactly
  as it already does for the gateway. **One process, not two** — the outbox drainer is a task inside
  the API process, for the reason Seam D gives.
- **Delete entrypoint step 5** ([`docker-entrypoint.sh:1183-1190`][docker-entrypoint-sh-1183-1190]) and
  `start_session_kv_server()` ([`platform_mcp_server.py:744-785`][platform_mcp_server-py-744-785]). One owner, no TOCTOU (R7). This
  is only safe once the supervisor runs at every replica count — see
  [`agent-process-supervisor.md`][agent-process-supervisor] §3.1 — because at `replicas: 1`
  those two are currently the only things that start the server at all. Deleting the MCP launcher
  also retires the salt asymmetry R7 now carries: the fallback server cannot pseudonymise, because
  the stdio MCP allowlist does not pass it `SESSION_KV_SALT`.

**This contradicts a comment in the entrypoint, deliberately, and phase 3 has to rewrite it
rather than leave it standing.**
[`docker-entrypoint.sh:239-242`][docker-entrypoint-sh-239-242]
justifies the `IS_BOOTSTRAP_PRIMARY` gate by asserting the opposite conclusion:

```bash
# What it gates is per-POD by design, not once-per-volume: the session KV server
# (each pod's event-watcher posts to its OWN 127.0.0.1:8699, so every pod must
# run one — across replicas "always primary" is the required answer here, not a
# bug) and the OTel service-name stamp ...
```

That is correct **given its premise**, which is that every pod runs a watcher. The premise is what
§4.3 changes. Once the watcher is lease-gated, a follower has no local client and therefore needs
no local server, and "every pod must run one" stops following. The comment is not wrong today; it
is load-bearing for an arrangement this design replaces, and a phase-3 diff that deletes the
server's launch without correcting the paragraph explaining why it must exist leaves the next
reader with two documents that disagree. Note the ordering this implies: **§4.3's watcher gating
must land before the launch is deleted**, which is why the migration table puts phase 2 ahead of
phase 3.

- The watcher's `--daemon-url` **stays** `127.0.0.1:8699`, because section 4.3 gates the watcher
  on the same lease. It gains a real token and bounded retry with backoff. Nothing needs to be
  published on the Service, and 8699 never leaves the pod.
- R1's "a stopped KV server is invisible" is closed by the supervisor's status file and the
  readiness probe that reads it, specified in
  [`agent-process-supervisor.md`][agent-process-supervisor]
  §3.4. **Closed by being reported, not by taking the pod out of service** — and that distinction
  is the design's, not a hedge here. The KV server is an **optional** process in its table: the
  gateway's plugins fail open without it, so a stopped KV server sets `degraded: true` while the
  pod stays Ready. Marking the leader NotReady instead would remove the only endpoint there is and
  turn "no incident triage" into "no agent", which is a worse outcome than the finding.

  Two probe designs are wrong here for different reasons, and both were tried in drafts. Probing
  `/healthz` on 8699 directly fails because followers run no KV server, so every follower would be
  permanently NotReady and rollouts at `replicas > 1` would stall. Probing it over `httpGet`
  fails even on the leader, because a probe's `httpGet` dials the pod IP and the server binds
  loopback. The supervisor design settled on an `exec` probe over a status file for both reasons;
  this design should not reinvent either.

- **Start the KV server before the gateway, and stop it after.** Under Seam A the plugins inside
  the gateway are clients, so the dependency now runs in that direction. They fail open, so a
  slow start costs attribution rather than availability — but the ordering should be deliberate
  rather than incidental.

### 4.1 Journal mode, and when it is safe to change it

With exactly one writer the file no longer needs multi-process WAL, which is unsupported on the
network filesystem the RWX volume gives it (R3). The end state on RWX is `journal_mode=TRUNCATE`
with `locking_mode=EXCLUSIVE`; WAL stays only on RWO.

Two things about getting there.

**Exclusive locking cannot ship before the last direct opener is gone.** The RWX volume is
exactly the `replicas > 1` case ([`platformagent_manifests.go:101-113`][platformagent_manifests-go-101-113]), so turning on
`locking_mode=EXCLUSIVE` while `store.py`, `bridge.py`, and `session_manager.py` still call
`sqlite3.connect` locks them out permanently — and `store.py` holds a long-lived connection
([`store.py:138-146`][store-py-138-146], opened at `:174-184`), so whichever side opens first simply wins. The migration therefore splits
the change in two: the WAL fix lands early, in every opener at once, and exclusivity lands only
after Seam A's port. In the interim the file is `journal_mode=TRUNCATE` with default locking,
which is multi-process safe and drops the `-shm` requirement that makes WAL wrong here.

**The mode is passed in, not sniffed** — and it is passed in on RWO too, rather than TRUNCATE
being applied everywhere. Nothing inside the container can see the volume's access mode. The
operator already computes it (`getDefaultStorageConfig`) and sets the environment variable the
server reads.

That conditionality is load-bearing rather than tidiness. R3 is a property of the **network**
filesystem, so it exists only on RWX; the default install is a single replica on RWO, where WAL is
both supported and doing real work. Between phases 1 and 5 that install still has four openers —
the server, `store.py`'s long-lived connection, `bridge.py` once per span, and `session_manager` —
and dropping WAL there means readers block writers on exactly the per-span path Seam A spends a
cache to keep off the wire. **Applying TRUNCATE unconditionally would buy a fix for a bug the
default install does not have, at the cost of a regression it would.** RWO keeps WAL throughout;
RWX converts at phase 1.

**The conversion has to be allowed to fail.** §2 establishes that leaving WAL is refused while any
peer holds the file open, and from phase 3 the supervisor can restart the KV server on its own
while the gateway is still up holding `store.py`'s connection — which is precisely a restart into
a peer. So the conversion is attempted, and a failure is logged and continued past, never fatal:
the file stays WAL for that lifetime and converts on the next restart that happens to be alone.
Making it fatal would reintroduce R8 through a new door, and turn the supervisor's own restart
into a crash loop on RWX. This is the one place where the start-order accident §2 identifies
stops being merely undocumented and starts being wrong to rely on.

### 4.2 Acquiring the file at failover

`locking_mode=EXCLUSIVE` means the incoming leader's KV server cannot open the database until the
outgoing one has closed it. [`agent-process-supervisor.md`][agent-process-supervisor] §3.5
makes that ordering hold in the absence of a partition, by requiring
`lease_duration > max_poll + Σ(per-process shutdown grace)` — the shutdown term is the **sum over
the process table**, not one process's grace, and adopting the KV server is exactly what makes it
a sum. It explicitly does not fence a partitioned-but-live leader, and on a network filesystem a
hard-killed holder's locks clear on the file server's schedule rather than the pod's.

So the server retries. Startup acquires the lock with exponential backoff over a bounded window —
60 s — logging each attempt, and fails only past it.

**What bounds that number is the restart cap, and not the readiness probe.** An earlier draft of
this section had it the other way round and was wrong twice over. A failed **readiness** probe
takes the pod out of the endpoint list; it never restarts a container — only liveness does, and
[`agent-process-supervisor.md`][agent-process-supervisor] §3.4 says so directly. And the
question does not arise anyway, because the KV server is an **optional** process in that design's
table: its absence sets `degraded: true` and leaves `ready` alone, so no amount of lock-waiting
can move the probe at all. The supervisor's 60 s readiness budget is sized against the longest
legitimate start of the _required_ process, which is the gateway.

The real constraint is the restart budget, and it is a rate rather than a duration —
`agent-process-supervisor.md` §3.3 charges a failed start to the same counter as a crash, and
gives up on an optional process past **5 in 300 s**. A KV server that waits its 60 s, exits, and is
restarted with 1→2→4→8 s backoff spends that budget in about 255 s and is then left stopped and
`degraded` rather than retrying. So the property to hold is:

```
lock_retry_window × RESTART_CAP  +  Σ backoff   ≥   the longest handover worth surviving
```

which at 60 s and a cap of 5 is a little over four minutes of total tolerance. That is the figure
to argue about, not the 60 s in isolation. **Preferably the wait should not exit at all** — a
process that keeps retrying internally is not a failed start, spends no budget, and reports its
own state through the log; exiting to be restarted converts a slow handover into a countdown
towards permanent degradation for no benefit. Section 7 asserts the tolerance arithmetic, not the
readiness inequality the earlier draft asked for.

### 4.3 The watcher runs on the leader too — so every caller is loopback

`session_store`, `session_otel_bridge`, `incident_context`, and the MCP servers all live in or
below the gateway process, which only the leader runs, and the KV server is a sibling under the
same supervisor. They are therefore always co-located with the server they call.

One caller is not in that list and has to be accounted for: the **dashboard container**. The
operator gives it `SESSION_KV_DB_PATH` ([`platformagent_manifests.go:2359-2361`][platformagent_manifests-go-2359-2361]) and mounts
`system-metadata` at the database directory (`:2345-2349`), and unlike everything above it runs
on every replica — including followers, which have no KV server. Whether `hermes dashboard`
actually opens the file is the open question, and it decides which of two things phase 1 does:
if it does not, the environment variable and the mount are unused configuration and get deleted
early with a golden-file assertion pinning their absence; if it does, it becomes an API caller
that fails open on followers, which is acceptable precisely because the Service selects the
leader and a follower's dashboard is not serving anyone. What it must not be is discovered during
phase 9, which is what the earlier draft's "drop the mount from every container" would have
done.

The Go watcher was the exception, because it runs in every replica — which is what forced a
Service-exposed port, a NetworkPolicy, and a cross-pod hop. **Gate the watcher on leadership as
well and that exception disappears: every caller is `127.0.0.1:8699`, and nothing crosses a pod
boundary at all.** No Service port for 8699, no DNS dependency, no NetworkPolicy needed for it,
and the highest-risk phase of the migration is deleted rather than mitigated.

It is also the right fix for a bug that exists today. Every replica currently runs a watcher, so
at `replicas > 1` one Kubernetes event produces N injects, N chat posts, and N model turns.
Server-side idempotency would collapse those after the fact; leader-gating prevents them. It also
divides the watch load and credential use against every target cluster by N.

**Two things have moved under this section since it was written, and neither changes the
conclusion.** The watcher gained leading-edge debouncing for the crash-loop family and for
transient image-pull failures (`--backoff-min-count`, `--imagepull-transient-min-count`, wired at
[`start-services.sh:200-201`][start-services-sh-200-201]). That reduces how many events become alerts; it does nothing about
the same event becoming N alerts on N replicas, which is a fan-out problem rather than a
noise problem — so the N-duplicate bug is untouched by it.

The second is a precedent worth knowing about. `EVENT_WATCHER_ENABLED=false`, from
`spec.harness.eventWatcher.enabled`, is now an emergency stop: [`start-services.sh:130-147`][start-services-sh-130-147] skips
the watcher entirely and logs loudly that no events are being watched. That establishes the
container tolerates having no watcher in it — the other two services carry on — which is
reassuring for lease-gating. It is **not** a model for it, though, and the difference matters:
the emergency stop is a start-time decision, while leadership changes while the process is
running. "Block, don't exit" below is still the required shape.

**A note on where the watcher actually runs**, since an earlier draft of this section got it
wrong and the error changes the operator work involved: the watcher is not a container of its
own. It runs inside `envoy-credential-proxy`, started by
[`start-services.sh:190-199`][start-services-sh-190-199]
alongside the credential runtime and Envoy — which is also where its flags live, not in the
operator:

```bash
/usr/local/bin/k8s-event-watcher \
  --cluster-name="${EVENT_WATCHER_CLUSTER_NAME:-}" \
  --profiles-dir="${CREDENTIAL_PROXY_WORKSPACE_ROOT:-/opt/data}/profiles" \
  --dedup-persist="${dedup_persist}" \
  --dedup-window="${WATCHER_DEDUP_WINDOW}" \
  --in-cluster \
  --daemon-url=http://127.0.0.1:8699 \
  --token-env=SESSION_KV_API_KEY \
  --owner=platform \
  --reason=Failed,FailedToDrainNode,CrashLoopBackOff,BackOff,ImagePullBackOff,ErrImagePull,OOMKilled || true
```

The operator says the watcher is not its own container at
[`platformagent_manifests.go:2511`][platformagent_manifests-go-2511], and that container mounts the shared data PVC at `homeDir`
(`:1937`), so anything the watcher needs on the shared volume is already reachable. The lease
gate of this section becomes another flag on that invocation, and `--token-env` shows the shape
the `ingest` token would take.

That correction matters most for `--dedup-persist`. The earlier draft argued it was unavailable
without new operator work; in fact **it is already passed**, at
[`start-services.sh:193`][start-services-sh-193], pointing into `${CREDENTIAL_PROXY_WORKSPACE_ROOT}/event-watcher` — the
shared PVC — with a comment at `:27-38` explaining that the container's own state directory is a
16Mi in-memory `emptyDir` and would lose the cache on exactly the pod restarts that matter. The
window is 24h since #640.

**Which surfaces a bug that leader-gating fixes for free.** `dedupPersistPath`
([`main.go:557`][main-go-557])
derives the snapshot path per _cluster_, not per _replica_, and `Snapshot`
([`dedup.go:274-282`][dedup-go-274-282])
writes through a fixed temp name:

```go
// Atomic write: temp file + rename so an interrupted write
// doesn't corrupt the persisted state.
tmp := c.persistPath + ".tmp"
if err := os.WriteFile(tmp, data, 0o600); err != nil {
	return fmt.Errorf("dedup: write %s: %w", tmp, err)
}
if err := os.Rename(tmp, c.persistPath); err != nil {
	return fmt.Errorf("dedup: rename %s → %s: %w", tmp, c.persistPath, err)
}
```

At `replicas > 1` on the RWX volume, every replica's watcher writes that same `.tmp` path and
renames it over the same target, on a 30 s timer ([`main.go:106`][main-go-106]). The comment is accurate about
what it claims — the sequence is atomic with respect to an _interrupted_ write. It is not atomic
with respect to a _second writer_, and nothing in the path is per-writer. Two replicas
snapshotting concurrently can publish a file one of them only partly wrote.

Leader-gating collapses N writers to one and the collision stops existing. Recording it here
because it is a live bug at `replicas > 1` that this design fixes incidentally, so it should not
be re-discovered as a regression during phase 2 — and because if phase 2 were ever abandoned, a
unique temp suffix would be the standalone fix.

**Gate on the existing Lease; do not hold a second election.** The watcher should watch
`<agent>-leader` and act only while `holder_identity == $HOSTNAME`. Two independent elections
could disagree, and the disagreement is exactly the state to avoid: a watcher running on a pod
with no KV server. This needs no new RBAC — the pod's ServiceAccount already has
`get`/`list`/`watch` on leases in the namespace (`buildPlatformLeaderRole`,
[`platformagent_manifests.go:2875-2879`][platformagent_manifests-go-2875-2879]), and the `event-watcher-ksa-token` projection
(`:2109-2113`, mounted at `:1936`) declares no custom audience, so it authenticates to the local
API server. That token is the one `rest.InClusterConfig` reads, which is what the watcher already
uses for the management cluster.

Three consequences to handle:

- **Block, don't exit.** A non-leader watcher must idle in its lease-watch loop. Exiting is
  wrong: `restartPolicy: Always` turns a clean exit into `CrashLoopBackOff` on every non-leader
  pod. Non-leaders still reserve the container's 50m/64Mi request while idle.
- **Failover leaves an event-coverage gap — the real cost of this change.** Today a surviving
  replica already holds established watch streams. Leader-only means the new leader must acquire
  the lease, start the watcher, and re-establish watches against every target cluster. Worse than
  the gap is the re-list on the other side of it: the dedup cache is in-memory, so a new watcher
  re-alerts on everything still within the events TTL. **Server-side deduplication is what
  handles that**, not the watcher's cache — see below.
- **The watcher must tolerate connection-refused, not just 5xx.** It and the KV server start on
  the same lease acquisition, so it will sometimes win the race. This is a prerequisite for
  moving the KV server under the supervisor, not a follow-up to it: from the moment the server
  stops being started by the entrypoint in every container, a follower's watcher is pointed at a
  port with no listener.

#### Deduplicate on the server, not in a file the watcher passes to itself

An earlier draft made `--dedup-persist` load-bearing: point it at the shared volume and the
incoming leader inherits the outgoing leader's cache. It then dismissed the idea on the grounds
that the snapshot path would need a new PVC mount. **Both the proposal and the dismissal were
written against facts that are no longer true** — as above, the flag is already passed and
already points at the shared volume, and no mount is needed because the container the watcher
runs in already has one.

So the honest version is narrower. The mechanism is present and costs nothing; what it does not
do is make the cache an _authority_. It remains a best-effort optimisation for three reasons that
have not changed: the snapshot interval is 30 s ([`main.go:106`][main-go-106]), so it was never lossless; the
incoming leader inherits at most a 30-second-stale view; and at `replicas > 1` today it is
subject to the concurrent-writer collision described above.

**Put the authority in the server instead.** Zone 3 already needs an idempotency index for R2 and
R11. Widen it: `POST /v1/alerts` deduplicates on `(cluster, uid, reason, message-hash)` within a
TTL, and a replay returns the original session rather than executing again. One authority, no
shared file, no new mount — and it collapses three separate problems into one mechanism, because
the same index handles the N-replica duplicate, the failover re-list, and the caller's own retry
across the blackhole.

**The server's TTL must be at least the watcher's dedup window**, which is 24 h since #640, and
the operator should derive one from the other rather than let two defaults drift the way
`SESSION_KV_CLEANUP_TTL_DAYS` and `SESSION_KV_RETENTION_DAYS` did (R5). The reason is the whole
point of the paragraph above: if the server forgets an event before the watcher does, then for the
interval between the two windows the watcher's in-memory cache is once again the only thing
preventing a duplicate — which is exactly the authority this section just moved off it. Sizing it
the other way costs nothing but rows.

`--dedup-persist` then stays what it already is: an optimisation that saves the wasted HTTP calls
after a handover, and not something correctness rests on.

Metrics are not a concern today: `--metrics-addr` defaults to empty ([`main.go:105`][main-go-105]) and nothing
sets it, so the watcher's Prometheus registry is dormant. If it is ever enabled, note that the
series would migrate between pods on failover.

**Loopback is still not a trust boundary**, and the design must not treat it as one. Every
container in the pod shares one network namespace — that is the premise of the port-8699
uniqueness argument in [`docker-entrypoint.sh:57`][docker-entrypoint-sh-57] — so the dashboard, the credential proxy, and
fluent-bit can all reach `127.0.0.1:8699`. The credential-isolation design exists precisely
because some of those are less trusted than the agent. Loopback callers authenticate with the
same tokens as anyone else; co-location buys locality, not privilege.

---

## 5. Retention

One owner (`retention.py`), and one window per store rather than the two _defaults_ that silently
disagree today: `SESSION_KV_SESSION_RETENTION_DAYS`, `SESSION_KV_INCIDENT_RETENTION_DAYS`, and
`SESSION_KV_QUOTA_RETENTION_DAYS`, all set explicitly by the operator. Each store gets its own
window because the data has genuinely different useful lifetimes — a triage report outlives a
routing row, and a spent-quota counter is a reporting artefact once its day has passed — which is
why collapsing to a single knob would have been the wrong reading of R5. What R5 is about is two
_owners_ disagreeing on one table (`SESSION_KV_CLEANUP_TTL_DAYS` at 14,
`SESSION_KV_RETENTION_DAYS` at 7, neither set by the operator), and that is what one owner fixes.

The third window is the `alert_quota` table #641 added. It is currently swept by the same
`CLEANUP_TTL_DAYS` as everything else (`cleanup_old_records`, L280-283), with a comment saying 14
days is chosen so that "what did we drop last week" still has an answer. That is a retention
_policy_, stated in a comment and implemented by reusing an unrelated constant; making it a named
window is the whole of the change.

Naming the knobs is not the same as exposing them. #641 set the precedent for how a tunable on
this server reaches the operator: `ALERT_DAILY_LIMIT_CRITICAL` / `_WARNING` / `_INFO` are entries
in the deployment-env allowlist ([`platformagent_manifests.go:2147-2149`][platformagent_manifests-go-2147-2149]) rather than fields on
the CRD, so a user tunes them through `spec.deployment.env` and the operator's own default stands
otherwise. The retention windows should follow it rather than invent a second convention.

GC runs on a timer in the leader, not opportunistically inside request handlers, against the new
indices (R5, R9). The old variable names are read for one release and logged as deprecated, so an
installation that set either of them does not silently change retention on upgrade.

---

## 6. Migration

Each phase is independently shippable and leaves the tree working.

Two rules order it. The API-only rule in Seam A concentrates all failure into one process, so
**everything that makes that process survivable ships first** — doing it the other way round buys
a clean boundary by making an outage worse. And **nothing takes an exclusive hold on the database
until the last other opener is gone**, which is why the journal-mode work is split across phases 1
and 6 instead of landing whole in the middle.

**The supervisor design's phases interleave with this table rather than preceding it**, and the
split is load-bearing enough to state once. [`agent-process-supervisor.md`][agent-process-supervisor] S1 and S2 — a
supervisor at every replica count, and the restart policy and probe — are prerequisites for
**phase 3**. So is its **S3**, the lease retiming, and an earlier draft of this paragraph said the
opposite: that S3 paired with phase 6, on the reasoning that phase 6 creates the exclusive hold
the guarantee exists for, and it claimed the supervisor design agreed. It does not — it says
"sequence it with S4", and its S4 _is_ phase 3.

**The supervisor design is right, and the reason is arithmetic this design owns.** Phase 3 is what
puts a second entry in the process table, and §3.5's shutdown term is the sum over that table, so
phase 3 is the moment 10 s becomes 20 s. With today's parameters `15 > 7 + 20` is false by twelve
seconds: the overlap between an outgoing leader still stopping its processes and an incoming one
already starting its own goes from about 2 s to about 13 s. Nothing holds an exclusive lock until
phase 6, so this is not a correctness break in the interim — it is two KV servers writing one
SQLite file for thirteen seconds instead of two, which default locking survives. It is still a
regression, it is still measurable, and shipping phase 3 without S3 means shipping it knowingly.
The 15 s of extra failover blackhole S3 costs is the price of not doing that, and it comes due at
phase 3 rather than phase 6.

Its **S4** is not a prerequisite at all — it is this table's phase 3, described from the
supervisor's side. One change, one implementation, two documents that each need it in their
sequence.

| Phase | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Risk                                                                                                                                                                                                                |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | Split into `session_kv/server/` — including `quota.py` for the third table — schema versioning (with the unstamped-database case), routing promoted to columns with its v1→v2 backfill, indices, `BEGIN IMMEDIATE`, unified retention, subprocess timeouts, leaf-module extraction. **On RWX only: `journal_mode=TRUNCATE`, converted by whichever opens first while it is alone, and tolerated when it cannot** (see §2 and §4.1 — RWO keeps WAL). Dashboard env/mount settled either way. | Low — the consumer edits are the journal-mode constant and nothing else; existing tests cover the rest                                                                                                              |
| 2     | **Lease-gate the watcher**, with connection-refused backoff. Fixes N-duplicate alerts on its own, and must precede phase 3 — after that, a follower's watcher has no local listener.                                                                                                                                                                                                                                                                                                        | Medium — Go change; failover behaviour needs soaking                                                                                                                                                                |
| 3     | **Survivability.** KV server becomes a supervised process (the supervisor design's S4); delete entrypoint step 5 and the MCP launcher, and rewrite the `IS_BOOTSTRAP_PRIMARY` comment §4 quotes. Retires the salt asymmetry in R7. **Ships with that design's S3**, because this is the phase that makes the shutdown term a sum.                                                                                                                                                           | Medium — entrypoint + operator, loopback-only throughout; +15 s failover blackhole arrives here                                                                                                                     |
| 4     | Trust **zones and scopes** on top of the single key #616 shipped; the second (`ingest`) token added to the existing chart-generated Secret; generalise `POST /v1/sessions` (idempotency key, prompt, skill) and widen its index to cover event dedup; publish `client.py`; old routes kept as deprecated aliases.                                                                                                                                                                           | Medium — but smaller than first scoped: authentication, the token plumbing, and the loopback bind already exist. `SESSION_KV_API_KEY` stays valid for one release, so the watcher no longer has to move in lockstep |
| 5     | **Port the three direct openers to `client.py`**, with the write-through cache and fail-open miss behaviour.                                                                                                                                                                                                                                                                                                                                                                                | Medium — only now is this safe                                                                                                                                                                                      |
| 6     | **`locking_mode=EXCLUSIVE` on RWX**, with the startup lock retry of §4.2. Only now is the server the last opener, and only now does S3's release-before-acquire guarantee have a consumer.                                                                                                                                                                                                                                                                                                  | Medium — first change that can fail at failover                                                                                                                                                                     |
| 7     | Add the `incident-triage` skill; shrink `_build_agent_query` to an invocation; extract `render.py`, `notify.py`, `triage.py`.                                                                                                                                                                                                                                                                                                                                                               | Medium — changes agent-visible prompt text                                                                                                                                                                          |
| 8     | Outbox replaces `BackgroundTasks`: in-process drainer, step-wise recovery, a terminal `failed` state, `GET /v1/outbox/stats`.                                                                                                                                                                                                                                                                                                                                                               | Medium                                                                                                                                                                                                              |
| 9     | Delete the deprecated aliases. Drop `SESSION_KV_DB_PATH` and the `system-metadata` mount from every container that no longer opens the file.                                                                                                                                                                                                                                                                                                                                                | Low — but see below                                                                                                                                                                                                 |

Phase 2 is what removes the need for a Service-exposed 8699 and the cross-pod hop that came
with it; an earlier draft of this design had that as a high-risk phase touching the operator and
the Go sidecar together, and lease-gating the watcher deletes it outright. It is worth shipping
early and on its own, because it is independently valuable — the duplicate-alert bug it fixes is
live today at `replicas > 1` — and because its failover behaviour is the one thing here that
needs observation rather than a test. It moved ahead of the survivability phase for a second
reason: phase 3 stops every container from starting its own KV server, and a watcher on a pod
without one has to already know how to wait.

Phase 9 is where the boundary becomes verifiable rather than aspirational. Today the operator
sets `SESSION_KV_DB_PATH` in the shared env block ([`platformagent_manifests.go:1416-1418`][platformagent_manifests-go-1416-1418]) and
again for the dashboard (`:2280-2282`), and mounts `system-metadata` into multiple containers
(`:1143`, `:1813`, `:2346`). Once nothing but the server opens the file, all of that can be
removed from every container except the one running it — and the fact that it _can_ be removed is
the proof that no other component is still reaching for SQLite.

---

## 7. Verification

### 7.0 What was prototyped, and what it changed

The storage mechanisms of Seam A, Seam D and section 5 were built as a prototype before this
design was finalised — `txn()`, the versioned schema, the outbox with step-wise recovery, the
retention collector, and Seam A's caching client — with no HTTP layer, because every claim under
test is about storage semantics. It lives in
[`session-kv-decomposition/`](https://github.com/gke-labs/kube-agents/tree/main/docs/designs/session-kv-decomposition)
next to this file and runs on the standard library alone:

```bash
cd docs/designs/session-kv-decomposition && python3 run_experiments.py
```

**One experiment falsified this document, and a second confirmed a claim it should not have.**
K2 tested §2's journal-mode constraint and found it wrong in both directions; §2 now states the
measured rule and phase 1 carries the sequencing requirement that follows from it. K1 confirmed
that `txn()` fixes R6, against a fixture that differs from the code in two ways. It models both
writers as read-modify-writes, where the real `store.py` writer blind-replaces the whole document;
and its safe path, `Store.put_metadata`, takes a _patch_ and does `meta.update(patch)`. So the
merge is what keeps both fields, and `txn()` is what stops the merge interleaving — the experiment
demonstrates the fix this design has now adopted while attributing the result to the one it had
proposed. Seam A carries the corrected reading, and re-running K1 against a blind-replace writer
is the way to see the difference. A prototype is only as good as its fixture, and that is the
failure mode to weigh the rest of this table against: these are checks on a model of the code, not
on the code.

| #   | Claim under test                      | Result                                                                                                                                                                                                         |
| --- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| K1  | R6, the lost update                   | **Confirmed, wrong fixture.** True of two read-modify-writes; `store.py` blind-replaces the blob, which `txn()` does not fix — see Seam A                                                                      |
| K2  | §2's journal-mode constraint          | **FALSIFIED.** Openers cannot oscillate — leaving `WAL` with a peer connected is refused outright                                                                                                              |
| K3  | §4.1, `locking_mode=EXCLUSIVE`        | **Confirmed.** A second opener gets `database is locked` until the holder closes                                                                                                                               |
| K4  | R5, two retention owners              | **Confirmed.** The 7-day delete removes a row the 14-day owner's own query had just kept                                                                                                                       |
| K5  | R9, missing indices                   | **Confirmed.** `EXPLAIN QUERY PLAN` gives `SCAN` today and `SEARCH … USING INDEX` with Seam A                                                                                                                  |
| K6  | R10, 32-bit ids                       | **Confirmed.** A duplicate is an `IntegrityError` — a 500, not a retry — and the key replays instead                                                                                                           |
| K7  | R2, at-most-once triage               | **Confirmed.** The in-memory flow loses three of four steps; the outbox resumes at the in-flight one                                                                                                           |
| K8  | Seam A's cache                        | **Confirmed.** Write-through avoids the first-span fetch; `thread_id` refresh and fail-open behave. Negative caching was exercised here and has since been dropped as redundant with the bounded refresh queue |
| K9  | R4, threadpool starvation             | **Modelled, not reproduced** — AnyIO is absent, so a bounded executor stands in for the 40-slot pool                                                                                                           |
| K10 | Seam A's unstamped-database migration | **Confirmed.** A database built by the old `store.py` DDL is adopted at v1 with its rows intact                                                                                                                |

Three things it deliberately does not cover, all needing something this environment does not have:
the HTTP layer and therefore every Seam B finding (S6 included), multi-writer behaviour on a real
network filesystem, and anything requiring a cluster — the watcher's lease-gating and the failover
gap of §4.3 among them. Those stay in the end-to-end checks below.

**Unit.** Extend `agents/platform/scripts/test_session_kv_server.py` into per-module tests. It has
grown a good deal since this design was drafted — #616 added `TestSessionKvServerAuth` (`:186`)
and #641 a quota suite — so the split has more to carry than "move the formatting tests", and
those two suites should land as `test_auth.py` and `test_quota.py` rather than being folded into
whatever module inherits their subject. The formatting tests move to `test_render.py` unchanged —
they should keep passing byte-for-byte, which is the check that Seam C was a pure move.

Reuse `test_declared_routes_are_all_covered` (`:224`) rather than replacing it: it already walks
the app's route table, and generalising it from "has the auth dependency" to "declares a zone" is
the cheapest available guard on Seam B's table.

New cases: idempotency-key replay returns `duplicate` without a second chat post; the `agent`
credential is rejected on a zone-3 route; a `session_id` containing `../` is rejected rather
than interpolated; the quota claim still fails open when the database is unwritable.

Two more that exist because this design got them wrong first, and a passing suite that omits them
would say nothing:

- **A `store.write` following a `register_routing` leaves `thread_id` and `chat_id` intact.** Write
  it as a plain sequence rather than a concurrent one — the clobber Seam A describes is an ordering
  bug, and a test that only runs the two in parallel can pass while the common case is broken. The
  concurrent version is still worth having for `txn()`, but it is the weaker of the two.
- **A replayed alert consumes no quota, and a claimed quota is not stranded by a replay.** The
  order of the idempotency check and `_claim_alert_quota` is not visible in either mechanism's
  tests, and both orderings look correct in isolation: claiming first leaks a slot on every retry,
  checking first is right. #641's cap fails open, so the wrong order degrades quietly into a cap
  that under-counts rather than into anything that fails.

**Boundary.** The API-only rule needs a test, or it decays the first time someone needs a value
in a hurry. Add a check that no module outside `session_kv/server/` imports `sqlite3` against
this database, and that `DEFAULT_SESSION_KV_DB_PATH` survives in exactly one place — it is
currently copy-pasted into four ([`store.py:23`][store-py-23], [`bridge.py:21`][bridge-py-21], [`session_manager.py:10`][session_manager-py-10],
[`platform_mcp_server.py:21`][platform_mcp_server-py-21]). Cheap to write, and it is the only thing that keeps the seam real.

**Client.** Cache behaviour is the risk Seam A introduces, so test it directly: **a write
followed immediately by a span produces an enriched span with no HTTP request at all** — the
write-through property, and the one that decides whether this is a port or a regression; a miss
emits a span without session attributes rather than blocking; a repeated miss for an unknown ID
does not issue a request per span; an entry cached without `thread_id` is re-fetched and an entry
with one is not; the cache evicts under its bound; the server being down degrades every caller to
fail-open rather than raising into a gateway hook.

**Write that first case across two client references rather than one.** Fetching the client twice
and asserting the same object is the direct test, but the one that matters is behavioural: a write
through the handle `session_store` would use, then a read through the handle `session_otel_bridge`
would use, and no request between them. A per-instance cache passes every other assertion in this
list and fails only that one — which is why the singleton is written into Seam A as a requirement
rather than left to whoever implements `client.py`.

**Schema and locking.** A fixture database created by the old `store.py` DDL — tables present,
`schema_version` absent — migrates to head rather than failing or re-creating, and the v1→v2 step
backfills `thread_id`/`chat_id`/`platform` out of the existing blob for rows written before the
columns existed. A WAL→TRUNCATE conversion attempted while a peer connection is open is logged and
survived rather than raised (§4.1) — the case a fresh-database test cannot reach, and the one a
supervisor restart reaches routinely.

Section 4.2's timing property is asserted in code rather than prose, in the form 4.2 actually
states it: `lock_retry_window × RESTART_CAP + Σ backoff` covers the handover the design claims to
tolerate. **Not** the readiness inequality an earlier draft asked for — the KV server is an
optional process and cannot move that probe, and asserting `60 < 60` against the supervisor's
shipped `failureThreshold × periodSeconds` would have failed on the first run.

**Go.** `injector_test.go` already asserts the headers are sent; add the server-side counterpart
so the assertion means something, plus retry-across-503 coverage.

**Skill.** `incident-triage` gets the same treatment as the other 36: a `SKILL.md`, and a pass
from the repository's `review-skill-quality` skill. The behavioural check is that a triage run
started through `POST /v1/sessions` with `"skill": "incident-triage"` produces the same report
shape the Python string produced — including #405's recommend-exactly-one-option rule, which is
part of the current output and therefore part of what "same shape" means. That is the evidence
Seam C was a move rather than a rewrite.

**Operator.** The readiness probe and the `Args` change belong to
[`agent-process-supervisor.md`][agent-process-supervisor] §6. What this design adds to
`platformagent_manifests_test.go` and the golden files in
`k8s-operator/internal/testing/testdata/platform/expected/` is the retention and journal-mode
environment, the minted-token Secret, and — whichever way section 4.3 resolves it — an assertion
pinning the dashboard's `SESSION_KV_DB_PATH` and `system-metadata` mount as present-and-used or
absent. Note that the golden files do **not** need a Service port or NetworkPolicy for 8699,
because lease-gating the watcher keeps all traffic on loopback — and the existing NetworkPolicy's
ingress allowlist (`:2986-3012`) should stay as it is, without 8699 being added to it.
`tests/test_docker_entrypoint.py` and `deploy/shared/entrypoint_gate_check.sh` both assert on step
5 and must be updated when it is deleted — [`entrypoint_gate_check.sh:313-324`][entrypoint_gate_check-sh-313-324] specifically asserts
that port 8699 is released, and its header at `:27-31` explains the assertion.

**End-to-end**, on the e2e cluster at `replicas: 2`:

```bash
# 1. Exactly one leader; the KV server and the watcher are both on it and nowhere else.
kubectl -n kubeagents-system get pods -l kubeagents.io/is-leader=true
#    On a NON-leader pod, 8699 has no listener and the watcher is idle, not restarting:
kubectl -n kubeagents-system get pod <follower> \
  -o jsonpath='{.status.containerStatuses[?(@.name=="event-watcher")].restartCount}'

# 2. The leader answers on loopback, and unauthenticated calls do not.
#    The 401 already holds on main (#616); what phase 4 adds is the third call,
#    where the `agent` credential is refused on a zone-3 route. Run all three:
#    the first two are the regression check that the zones did not loosen
#    anything, and only the third is new.
kubectl -n kubeagents-system exec <leader> -c platform-agent -- \
  curl -sf 127.0.0.1:8699/healthz
kubectl -n kubeagents-system exec <leader> -c platform-agent -- \
  curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8699/v1/incidents \
  -d '{"chat_id":"x","thread_id":"y","report":"z"}'          # expect 401
kubectl -n kubeagents-system exec <leader> -c platform-agent -- \
  curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $AGENT_TOKEN" \
  127.0.0.1:8699/v1/alerts -d '{}'                           # expect 403

# 3. One event, one alert — the check that leader-gating fixed the N-duplicate bug.
#    Trigger a scenario and count chat messages; expect exactly one.

# 4. Kill the leader mid-incident. Expect no duplicate alert after the handover
#    (server-side event dedup) and no lost one (outbox + idempotency). The incoming
#    KV server may log lock-acquisition retries; it must not exceed its window.
kubectl -n kubeagents-system delete pod <leader>

# 5. Outbox drains after that restart. Via the API, not sqlite3: the database is
#    opened exclusively from phase 6 on, so a second process cannot read it.
kubectl -n kubeagents-system exec <pod> -c platform-agent -- \
  curl -sf -H "Authorization: Bearer $AGENT_TOKEN" 127.0.0.1:8699/v1/outbox/stats
```

Step 4 is the one that needs soaking rather than a single run: the failover gap in section 4.3
is a timing property, and one green run does not characterise it.

The stockout scenarios under `agentplugins/gke-stockout-investigator/scenarios/` are the
existing way to generate real warning events for step 3.

**Docs.** `agents/platform/docs/session_management.md` and
`docs/designs/gchat-session-metadata-data-flow.md:89-111` both describe the current launch path
and schema. They go stale in pieces rather than all at once: the schema at phase 1 — including
the `incidents.created_at` versus `updated_at` mismatch that is R12, which is a documentation fix
and nothing else — the two-launcher description at phase 3, the `GET /v1/sessions` route at phase
4, and the triage prompt at phase 7. Run the `review-docs-drift` skill and `make docs-check` at
each.

---

## 8. Deliberate non-goals

- **No external database, and no in-cluster Postgres either — decided here, not inherited.**
  This bullet used to decline _Cloud SQL_ on grounds of a dependency and an IAM surface, which
  stopped covering the option actually on the table once the repository grew an **in-cluster**
  Postgres for the Hindsight memory provider (`k8s-operator/config/integrations/hindsight/`). That
  has no IAM surface and on a stock install is already running, so feasibility was no longer the
  argument and the call had to be re-made.

  It is a bigger decision than a storage swap, because **the single-writer requirement is what
  most of the supervisor design exists to serve**. With Postgres there is no exclusive file to
  hand over, so §4.1 and §4.2 disappear, the outbox drains with `SELECT … FOR UPDATE SKIP LOCKED`,
  GC is an idempotent `DELETE`, and event dedup is the unique index §4.3 already calls the real
  authority — nothing needs the leader for correctness. `agent-process-supervisor.md` §3.8A works
  the consequences out from its side. That is a substantial simplification and it is the reason
  this bullet is argued rather than asserted.

  **It is declined on operations, and each of the three reasons is sufficient on its own.**
  Hindsight's Postgres **deploys only when the install asked for that memory provider**, so putting
  sessions there makes it mandatory for installs that chose the file-based provider or no memory at
  all — and keeping SQLite as a fallback means maintaining two backends, which is worse than
  either. It is a **single-replica StatefulSet**, so this trades a file on a PVC for one pod plus a
  network hop on the alert-and-triage path, which is precisely what must keep working during a
  cluster incident; the failure mode is losing incident triage exactly when a cluster is unhealthy.
  And it runs `POSTGRES_HOST_AUTH_METHOD=trust` with no password behind a NetworkPolicy its own
  README notes is enforced only on Dataplane V2 clusters — justified there by the data belonging to
  pods already trusted with it, which session metadata is not: S5 and #616 pseudonymised chat
  identifiers under a salt deliberately, and moving those rows behind trust auth would spend that
  work.

  **Settled: the store stays a file.** Section 6 assumes it, and that assumption is now
  load-bearing rather than provisional — phases 1 and 6 and the supervisor's S3 are all priced
  against it. `agent-process-supervisor.md` Q5 asks the same question from the other side and
  closes by reference to this bullet; the two designs deferring to each other is what kept it open
  through two revisions, and it is the reason this is written as a decision rather than as an
  option. **What would reopen it** is narrow and worth naming: an in-cluster Postgres that ships
  unconditionally, replicated, and authenticated. Short of all three, the answer does not change.

- **No request-continuous HA.** The failover blackhole in [`leader_elect.py:12-16`][leader_elect-py-12-16] is inherited,
  not fixed — and the supervisor design widens it, deliberately, to make the file handover
  well-defined. Idempotency plus caller retry makes it survivable, not invisible.
- **No fencing of a partitioned leader.** The Lease says who should be leading, not what is still
  executing. `agent-process-supervisor.md` §3.6 bounds the overlap; the startup lock retry and the
  idempotency index are what make it harmless. A fencing token would need a second store to hold
  it, which is the dependency the first bullet declines.
- **Request-continuous event coverage.** Lease-gating the watcher (section 4.3) trades N
  duplicate alerts for a gap around failover. Server-side event deduplication bounds the damage;
  closing the gap entirely would need a standby watcher holding warm streams without injecting,
  which is more machinery than the problem justifies.
- **No metrics endpoint on the KV server** (R13's second half). The logging half is fixed as a
  side effect of the supervisor owning the process — output goes to inherited stderr and reaches
  fluent-bit, instead of an unbounded file on the PVC — but nothing here exports series. The
  watcher's own registry is dormant for the same reason (`--metrics-addr` unset), so adding one
  here would be the first, and it belongs with that decision rather than inside this
  decomposition.

---

## 9. Where each finding is closed

Section 1 lists twenty-three findings. This is where each one lands, so that none of them can
quietly fail to be addressed. Four were closed before this design started; they keep their rows
so that the count still reconciles.

| Finding                      | Closed by                                                                                                                                                              | Phase   |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| S1 no auth, binds `0.0.0.0`  | **Already shipped** (#616): `verify_api_key` on every data route, loopback bind. Residue — one key, no scopes — by Seam B zones                                        | done; 4 |
| S2 stored-report injection   | Seam B zone 2; Seam C fencing of the prepended report                                                                                                                  | 4, 7    |
| S3 event-data injection      | Seam C — authority moves into the skill, event fields fenced and labelled                                                                                              | 7       |
| S4 auth theatre              | **Already shipped** (#616): watcher sends a real key via `--token-env`, server verifies it                                                                             | done    |
| S5 PII enumeration           | **Already shipped** (#616): route authenticated, identities pseudonymised. Residue — the enumeration itself — by removing `GET /v1/sessions`                           | done; 4 |
| S6 path traversal            | Seam B — server mints IDs; `{id}` resolved against the store before use                                                                                                | 4       |
| S7 metadata trusted on read  | Seam B — routing writes in zone 3 behind the `ingest` credential, shape-checked at the write against typed columns                                                     | 1, 4    |
| S8 free LLM turns            | **Already shipped** (#641): per-severity daily cap. Residue — fail-open, alert path only — by Seam B zone 3                                                            | done; 4 |
| S9 subprocess env and logs   | Seam D — `notify.py` environment allowlist, redacted failure logging                                                                                                   | 7       |
| S10 no size caps             | Seam C — length cap and control-character stripping                                                                                                                    | 7       |
| R1 unsupervised, unmonitored | `agent-process-supervisor.md` §3.3–3.4 — supervised and restarted; reported as `degraded` rather than by going NotReady, since the server is an optional process there | 3       |
| R2 at-most-once triage       | Seam D outbox; idempotency key on session creation                                                                                                                     | 8, 4    |
| R3 multi-writer WAL on NFS   | §4.1 — `TRUNCATE` early, `EXCLUSIVE` after the port                                                                                                                    | 1, 6    |
| R4 threadpool starvation     | Subprocess timeout in phase 1, where the split lands it; Seam D's dedicated bounded executor in phase 8                                                                | 1, 8    |
| R5 two retention policies    | §5 — one owner, one explicit window per store                                                                                                                          | 1       |
| R6 lost updates              | Seam A — routing promoted to columns, so the two writers stop overlapping; `txn()` with `BEGIN IMMEDIATE` for the read-modify-write that remains                       | 1       |
| R7 two launchers, one port   | §4 — entrypoint step 5 and the MCP launcher deleted; retires the salt asymmetry too                                                                                    | 3       |
| R8 `init_db()` at import     | Seam A — schema work and the identity purge move into the app factory's startup                                                                                        | 1       |
| R9 missing indices           | Seam A — indices on the timestamp columns; timer-driven GC                                                                                                             | 1       |
| R10 32-bit session IDs       | Seam B — 128-bit IDs, unique index on the idempotency key                                                                                                              | 4       |
| R11 no gateway retry         | Seam D — outbox retry with backoff                                                                                                                                     | 8       |
| R12 schema drift in docs     | §7 docs — a documentation fix, not a code one                                                                                                                          | 1       |
| R13 logs and metrics         | Logging via the supervisor's inherited stderr; **metrics declined**, §8                                                                                                | 3, —    |

Two findings surfaced by the 2026-08-13 re-verification are not in the original twenty-three and
are recorded here so they are not lost:

| Finding                                    | Closed by                                                                        | Phase |
| ------------------------------------------ | -------------------------------------------------------------------------------- | ----- |
| Concurrent dedup-snapshot writers at `n>1` | §4.3 — lease-gating collapses N watchers to one; unique temp suffix if abandoned | 2     |
| `alert_quota` has no module or window      | §3 `quota.py`; §5 `SESSION_KV_QUOTA_RETENTION_DAYS`                              | 1     |

And three more from the 2026-08-17 review, which are defects in this design rather than in the
code — recorded because a design that fixed them silently would leave no trace that the reasoning
had changed:

| Finding                                                                   | Resolved by                                                                                                         |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| R6's stated fix did not fix R6 — `store.py` blind-replaces the whole blob | Seam A, "Serialising the two writers…" — routing moves to columns; prototype K1 modelled the wrong shape            |
| No route for the metadata write phase 5 depends on                        | Seam B — `PUT /v1/sessions/{id}/metadata`, zone 2                                                                   |
| §4.2's readiness-probe constraint was wrong and unsatisfiable             | §4.2 — an optional process cannot move readiness; the restart budget is the real bound, and §7 asserts that instead |

<!-- The companion design is not on `main` yet, so this points at its pull request
     rather than at a path that would 404. One edit swaps it for the relative link
     `agent-process-supervisor.md` when #730 merges, at which point `make docs-check`
     starts verifying it. -->

[agent-process-supervisor]: https://github.com/gke-labs/kube-agents/pull/730

<!-- Source links, line-anchored and pinned to the commit these line numbers
     were read from (ebe33ba). Re-pin here when the numbers are refreshed. -->

[__init__-py-29-34]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/plugins/incident_context/__init__.py#L29-L34
[__init__-py-38]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/plugins/incident_context/__init__.py#L38
[__init__-py-40-45]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/plugins/incident_context/__init__.py#L40-L45
[__init__-py-47]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/plugins/incident_context/__init__.py#L47
[adapter-py-958]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agentplugins/pubsub-platform/files/platforms/pubsub/adapter.py#L958
[adapter-py-988]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agentplugins/pubsub-platform/files/platforms/pubsub/adapter.py#L988
[bridge-py-136]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_otel_bridge/bridge.py#L136
[bridge-py-21]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_otel_bridge/bridge.py#L21
[bridge-py-56]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_otel_bridge/bridge.py#L56
[dedup-go-274-282]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/cmd/k8s-event-watcher/dedup.go#L274-L282
[docker-entrypoint-sh-1183-1190]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/docker-entrypoint.sh#L1183-L1190
[docker-entrypoint-sh-1190]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/docker-entrypoint.sh#L1190
[docker-entrypoint-sh-239-242]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/docker-entrypoint.sh#L239-L242
[docker-entrypoint-sh-57]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/docker-entrypoint.sh#L57
[entrypoint_gate_check-sh-313-324]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/entrypoint_gate_check.sh#L313-L324
[leader_elect-py-12-16]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/leader_elect.py#L12-L16
[leader_elect-py-138]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/leader_elect.py#L138
[leader_elect-py-60-61]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/leader_elect.py#L60-L61
[main-go-105]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/cmd/k8s-event-watcher/main.go#L105
[main-go-106]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/cmd/k8s-event-watcher/main.go#L106
[main-go-557]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/cmd/k8s-event-watcher/main.go#L557
[platform-agent-secret-yaml-6-25]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/charts/kube-agents/templates/platform-agent-secret.yaml#L6-L25
[platform_mcp_server-py-21]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/platform_mcp_server.py#L21
[platform_mcp_server-py-30-41]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/platform_mcp_server.py#L30-L41
[platform_mcp_server-py-698]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/platform_mcp_server.py#L698
[platform_mcp_server-py-700-712]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/platform_mcp_server.py#L700-L712
[platform_mcp_server-py-744-785]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/platform_mcp_server.py#L744-L785
[platformagent_controller-go-496-504]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_controller.go#L496-L504
[platformagent_controller-go-532]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_controller.go#L532
[platformagent_manifests-go-101-113]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L101-L113
[platformagent_manifests-go-1410]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L1410
[platformagent_manifests-go-1416-1418]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L1416-L1418
[platformagent_manifests-go-1611]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L1611
[platformagent_manifests-go-2147-2149]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2147-L2149
[platformagent_manifests-go-2279-2282]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2279-L2282
[platformagent_manifests-go-2318-2340]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2318-L2340
[platformagent_manifests-go-2359-2361]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2359-L2361
[platformagent_manifests-go-2511]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2511
[platformagent_manifests-go-2828]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2828
[platformagent_manifests-go-2875-2879]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L2875-L2879
[platformagent_manifests-go-3078]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L3078
[platformagent_manifests-go-53]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/k8s-operator/internal/controller/platformagent_manifests.go#L53
[session_kv_server-py-116-125]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_kv_server.py#L116-L125
[session_kv_server-py-227]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_kv_server.py#L227
[session_kv_server-py-47-53]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_kv_server.py#L47-L53
[session_kv_server-py-73-100]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_kv_server.py#L73-L100
[session_management-md-136]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/docs/session_management.md#L136
[session_manager-py-10]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_manager.py#L10
[session_manager-py-208]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_manager.py#L208
[session_manager-py-59]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/session_manager.py#L59
[start-services-sh-130-147]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/start-services.sh#L130-L147
[start-services-sh-139]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/start-services.sh#L139
[start-services-sh-190-199]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/start-services.sh#L190-L199
[start-services-sh-193]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/start-services.sh#L193
[start-services-sh-200-201]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/deploy/shared/start-services.sh#L200-L201
[store-py-117-122]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L117-L122
[store-py-24-30]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L24-L30
[store-py-138-146]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L138-L146
[store-py-156]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L156
[store-py-178]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L178
[store-py-148-171]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L148-L171
[store-py-178-179]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L178-L179
[store-py-179]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L179
[store-py-212]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L212
[store-py-23]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L23
[store-py-26]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/chat/defaults/plugins/session_store/store.py#L26
[test_session_kv_server-py-238]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/test_session_kv_server.py#L238
[test_session_kv_server-py-27-76]: https://github.com/gke-labs/kube-agents/blob/ebe33bafa4608f900348623cf8943fdeb15d701f/agents/platform/scripts/test_session_kv_server.py#L27-L76
