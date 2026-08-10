# Session KV Decomposition

> **STATUS — draft; not implemented.** Nothing here ships today. `session_kv_server.py` is
> described in section 1 as it currently exists; sections 3 onward are the proposal.

**Scope:** The `session_kv` mechanism — the SQLite store at
`/var/lib/kube-agents/session/session_kv.db`, the HTTP server on port 8699, and the six
components that read or write them.
**Owns:** the decomposition of `session_kv_server.py`, the trust boundary around port 8699,
retention policy ownership, and the leader-gated single-writer contract.
**Does not own:**

- The container's process model — what starts a long-lived process, restarts it, and reports its
  health. [`agent-process-supervisor.md`](agent-process-supervisor.md) owns that, and this design
  depends on it: the KV server becomes a supervised child there, and the readiness signal, the
  restart policy, and the lease timing guarantee at failover are all cited from it rather than
  restated here. **Its phases S1–S3 are prerequisites for phase 3 below.**
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

## 1. What exists today

`agents/platform/scripts/session_kv_server.py` is 447 lines and a single FastAPI app. It owns
ten unrelated responsibilities:

| #   | Responsibility                     | Where                                                                                                   |
| --- | ---------------------------------- | ------------------------------------------------------------------------------------------------------- |
| 1   | Schema DDL                         | `init_db()`, L44-70 — and again in `session_store/store.py:13`                                          |
| 2   | Retention GC                       | `cleanup_old_records()`, L77-84                                                                         |
| 3   | Session-ID minting                 | `POST /sessions`, L92-105                                                                               |
| 4   | Session metadata reads             | L369-416                                                                                                |
| 5   | Incident report store              | L419-444 — a second, unrelated table                                                                    |
| 6   | Alert text formatting              | `clean_workload_name` / `clean_reason_label` / `clean_event_message` / `get_severity_details`, L108-155 |
| 7   | Chat delivery + thread-key parsing | `_post_initial_alert()`, L176-199                                                                       |
| 8   | Session↔thread routing             | `_register_session_routing()`, L202-225                                                                 |
| 9   | LLM prompt authoring               | `_build_agent_query()`, L248-284                                                                        |
| 10  | Gateway orchestration              | `_create_gateway_session` / `_start_agent_turn`, L228-329                                               |

Six components depend on it, and only three go through the HTTP API:

| Consumer                            | Path                         | Reads / writes                   |
| ----------------------------------- | ---------------------------- | -------------------------------- |
| `k8s-event-watcher` (Go sidecar)    | HTTP `127.0.0.1:8699`        | `POST /sessions`, `/inject`      |
| `platform_mcp_server.py:531,558`    | HTTP `127.0.0.1:8699`        | metadata read, incident write    |
| `incident_context/__init__.py:37`   | HTTP `127.0.0.1:8699`        | incident read                    |
| `session_manager.py:59`             | **direct `sqlite3.connect`** | metadata read                    |
| `session_otel_bridge/bridge.py:107` | **direct `sqlite3.connect`** | metadata read, **once per span** |
| `session_store/store.py:159`        | **direct `sqlite3.connect`** | metadata write, own retention    |

The HTTP API is therefore not a boundary. Half the consumers open the file directly, two of
them hand-roll the same `CREATE TABLE`, and two enforce different retention on the same rows.

### 1.1 Security findings

| ID  | Severity | Finding                                                                                                                                                                                                                                                                                                                            |
| --- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1  | High     | **No authentication on any route**, and the server binds `0.0.0.0` (`docker-entrypoint.sh:428`). There is no NetworkPolicy on the pod — the legacy one is deleted at `platformagent_controller.go:446`. Any pod in the cluster can reach `podIP:8699`.                                                                             |
| S2  | High     | **Stored-report prompt injection.** `POST /v1/incidents` (L419) is an unauthenticated write. `incident_context` prepends the stored `report` verbatim to the user's next message in that thread (`__init__.py:28-33`), into an agent whose prompt says it is "explicitly authorized to create a branch … and open a Pull Request." |
| S3  | High     | **Event-data prompt injection.** `_build_agent_query` (L248) interpolates `message`, `name`, and `namespace` — attacker-controllable by anyone who can create a pod or event in a watched cluster — into that same GitOps-authorizing prompt, unfenced.                                                                            |
| S4  | Medium   | **Declared auth is theatre.** The watcher sends `Authorization: Bearer` and `X-Asserted-Caller` (`injector.go:85-88`); the server reads neither. The token is the hardcoded literal `cluster-internal-trusted` (`platformagent_manifests.go:2110`).                                                                                |
| S5  | Medium   | **PII disclosure.** `GET /v1/sessions` (L389) returns every session's `user_email`, `user_resource`, and chat space, unauthenticated and enumerable.                                                                                                                                                                               |
| S6  | Medium   | **Path traversal into the gateway.** The `session_id` path param is unvalidated and interpolated into `f"{api_url}/api/sessions/{session_id}/chat"` (L291), reaching arbitrary gateway API routes.                                                                                                                                 |
| S7  | Low      | **Metadata is trusted on read.** `send_notification` builds `target = f"{platform}:{chat_id}:{thread_id}"` from stored metadata (`platform_mcp_server.py:543`). Anyone who can write metadata redirects the agent's reports to a chat space of their choosing.                                                                     |
| S8  | Low      | **Free LLM turns.** `POST /sessions/{id}/inject` triggers a chat post and a model turn with no auth and no rate limit — a cost-amplification primitive.                                                                                                                                                                            |
| S9  | Low      | Subprocess stdout/stderr from `hermes send` is logged on failure (L196); `_run_env()` passes the full environment, tokens included, to every subprocess.                                                                                                                                                                           |
| S10 | Low      | No size cap on `report` or `metadata`; no escaping of alert text before it reaches chat.                                                                                                                                                                                                                                           |

### 1.2 Resilience findings

| ID  | Severity | Finding                                                                                                                                                                                                                                                                                                                                         |
| --- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | High     | **Unsupervised and unmonitored.** Started with `&` (`docker-entrypoint.sh:428`) and never restarted. The gateway container has **no readiness or liveness probe at all** (`platformagent_manifests.go:1917-1936`), so `/healthz` is dead code and a dead KV server is invisible.                                                                |
| R2  | High     | **At-most-once triage.** `BackgroundTasks` (L364) holds the whole flow in memory. A restart between `200 {"status":"injected"}` and the gateway call loses the incident silently — and the watcher, having got its 200, dedup-suppresses the retry for 5 minutes.                                                                               |
| R3  | High     | **Multi-writer SQLite on NFS.** At `replicas > 1` the operator moves the volume to RWX / `standard-rwx` (`platformagent_manifests.go:73-89`). SQLite WAL requires shared memory and is unsupported on network filesystems. Every replica runs its own KV server, because entrypoint step 5 precedes `exec "$@"` and so never sees the election. |
| R4  | Medium   | **Threadpool starvation.** Sync endpoints share AnyIO's 40-slot pool. `_start_agent_turn` holds a slot for up to 300 s and `hermes send` has **no timeout at all**. One hang blocks `/v1/incidents/by-thread`, whose caller gives up at 2 s and fails open — users' replies silently lose context.                                              |
| R5  | Medium   | **Two retention policies, one table.** The server deletes at 14 days (`SESSION_KV_CLEANUP_TTL_DAYS`, L41); `session_store` deletes at 7 (`SESSION_KV_RETENTION_DAYS`, `store.py:99-103`) on every write. The operator sets neither, so 7 silently wins over rows the server believes it keeps.                                                  |
| R6  | Medium   | **Lost updates.** `_register_session_routing` (L202) is a read-modify-write with no `BEGIN IMMEDIATE`, racing `INSERT OR REPLACE` from `store.py:135-142` on the same `session_id`.                                                                                                                                                             |
| R7  | Medium   | **Two launchers, one port.** `platform_mcp_server.py:577-581` probes the port and spawns if free — a TOCTOU race against entrypoint step 5. The loser dies of `EADDRINUSE` into a logfile nobody reads.                                                                                                                                         |
| R8  | Low      | `init_db()` runs at import (L447); a failure makes the module unimportable and the server silently absent.                                                                                                                                                                                                                                      |
| R9  | Low      | No index on `updated_at` / `created_at`: every GC `DELETE` and the `ORDER BY` in `list_sessions` is a full scan. GC only runs on writes, so a quiet system never collects and a busy one scans on every insert.                                                                                                                                 |
| R10 | Low      | `uuid4().hex[:8]` is 32 bits and the insert is a plain `INSERT` (L95-103) — a collision is a 500, not a retry.                                                                                                                                                                                                                                  |
| R11 | Low      | `_create_gateway_session` has no retry. The KV server is listening before `hermes gateway run` binds 8642, so alerts in the startup window are dropped with a log line.                                                                                                                                                                         |
| R12 | Low      | Schema drift: `incidents.created_at` in code vs `updated_at` in `agents/platform/docs/session_management.md:97`. That doc's Phase 1 also claims the proxy stores the triage report, which it does not — `send_notification` does, later.                                                                                                        |
| R13 | Low      | Unbounded logfile on the PVC; no metrics of any kind, while the watcher that feeds it is fully instrumented.                                                                                                                                                                                                                                    |

---

## 2. Design constraints

- **`PYTHONPATH=/opt/defaults/scripts` is set pod-wide** (`platformagent_manifests.go:1347-1350`),
  and `agents/platform/scripts/` is copied there (`Dockerfile:187`). A package placed there is
  importable from the Chat Agent's plugins, the Platform Agent's plugins, and the MCP servers
  alike. This is what makes a single shared storage library possible without vendoring.
- **The election supervises a child, but not everywhere.** `leader_elect.py:138` starts
  `hermes gateway run` on acquire and terminates it on loss (L143-153); it labels the pod
  `kubeagents.io/is-leader=true` and the Service selects on that label. A second child process
  gets single-writer semantics and a leader-routed network path for free — **but only at
  `replicas > 1`**, because the operator makes the script the container's exec target only in
  that branch (`platformagent_manifests.go:1876-1880`), and the script itself `execvp`s the
  gateway when the lease environment is absent (`leader_elect.py:60-61`). At the default single
  replica there is no supervisor to be a child of.
  [`agent-process-supervisor.md`](agent-process-supervisor.md) closes that; this design assumes
  its S1–S3 have shipped.
- **Failover blackholes traffic.** `leader_elect.py:12-16` says so explicitly: zero ready endpoints
  until the new leader labels itself. Anything crossing that window must be retried by the caller
  and deduplicated by the server. The supervisor design widens that window to buy a release-before-
  acquire guarantee for exclusively-held resources — which is what makes the file-lock handover in
  section 4 well-defined.
- **`journal_mode` is a property of the file, not of the connection.** Every process that opens
  the database has to agree on it, and the last one to set it wins. `session_kv_server.py:50` and
  `store.py:122` both set `WAL` today; during any window where more than one opener exists, they
  must set the same non-WAL mode or they will flip the file back and forth under each other.
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
    outbox.py      #   durable work queue + its in-process drainer (see Seam D)
    retention.py   #   one policy, one collector
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

| Component                           | Today                      | After                                           |
| ----------------------------------- | -------------------------- | ----------------------------------------------- |
| `session_store/store.py:159`        | `sqlite3.connect` + DDL    | `client.put_session_metadata()`                 |
| `session_otel_bridge/bridge.py:107` | `sqlite3.connect` per span | `client.session_metadata()`, cached — see below |
| `session_manager.py:59`             | `sqlite3.connect`          | `client.session_metadata()`                     |

`client.py` ships to `/opt/defaults/scripts` and is importable from the Chat Agent's plugins,
the Platform Agent's plugins, and the MCP servers alike (`platformagent_manifests.go:1347-1350`).
It carries the caller's token, a short timeout, typed errors, and an injectable transport so
tests do not need a live server. Being the single client, it is also the single place that
implements retry, caching, and fail-open policy — today each of the five callers improvises its
own, with timeouts of 2 s, 3 s, and none.

`schema.py` gains a `schema_version` table with explicit migrations, indices on the timestamp
columns, and a `txn()` helper issuing `BEGIN IMMEDIATE`. `BEGIN IMMEDIATE` is the fix for R6
whether or not two writers currently collide on one row: a read-modify-write with no explicit
transaction is unsafe by construction, and `_register_session_routing` is one. With `schema.py`
as the only DDL, the duplicated `CREATE TABLE` — responsibility #1 of section 1, hand-rolled a
second time at `store.py:13` — stops existing. (R12 is a different problem, code-versus-docs
drift, and it is fixed in section 7 by updating the doc.)

**Migrations have to adopt a database nobody stamped.** Every live PVC already carries a
`session_metadata` table created by `CREATE TABLE IF NOT EXISTS` in one of two places, and no
`schema_version` row anywhere. The runner therefore needs three cases, not two: no tables → create
at head; tables but no `schema_version` → stamp as v1 and continue; otherwise → migrate forward.
The middle case is the one a fresh-database test will not cover, so it gets a fixture built by the
old `store.py` DDL.

The two tables stay logically separate stores with separate owners and separate retention —
`SessionStore` belongs to chat ingress, `IncidentStore` to the triage flow. They share a file
only as an implementation detail below the API.

**One import-graph change comes with this.** `session_kv_server.py` imports `agent_common_server`
for `_run_env` and the config paths, and `agent_common_server.py:15,24` imports `SessionManager`
and constructs one at module scope. Once `SessionManager` is a `client.py` consumer, the server
process builds an HTTP client to itself at import time. Move `_run_env` and the path constants
into a leaf module that imports nothing from this tree, and have `session_kv/server/` depend on
that instead — which also keeps the boundary check in section 7 from having to special-case the
server's own transitive imports.

#### The OTel hot path

`session_otel_bridge` is the one consumer where this constraint is not free. It resolves
metadata **once per span** (`bridge.py:45-56`), so a naive swap turns a local file read into a
network round trip inside span creation — strictly worse than what it replaces, and on a path
that must never block or throw.

The client therefore has to make the bridge's usage a cache hit almost always:

- **The cache is write-through, and that is what carries the first span.** A read-through cache
  alone would be a regression, not a port: `log_event_to_db` is a `pre_gateway_dispatch` hook, so
  today the metadata is in SQLite _before_ the gateway dispatches and the very first span of a
  session already resolves. Under a read-through cache that span is a guaranteed miss — and it is
  the root span of the turn. So `client.put_session_metadata()` populates the cache on success.
  Writer and reader are the same process, and the write happens first, so the hit is structural
  rather than lucky.
- A TTL cache keyed by `session_id`, bounded (LRU, order 1024 entries) so that a long-lived
  gateway cannot accumulate one entry per session seen. Session metadata is near-immutable —
  written once at session creation, amended once when `thread_id` is resolved — so a 60 s TTL is
  generous.
- **Miss is fail-open, not fetch-inline.** On a miss the bridge emits the span without the
  session attributes and schedules an out-of-band refresh on a bounded queue that drops rather
  than blocks, so the next span is enriched. Adding request latency to span creation is not an
  option; neither is raising out of it.
- Negative caching, so an unknown session ID cannot generate a request per span. Its TTL is much
  shorter than the positive one — order 5 s — and any write for that `session_id` clears it,
  because the common cause of a negative entry is a span that arrived just ahead of the write.
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

`session_store`'s write hook has the same shape of risk on a colder path: it runs on every
inbound gateway message (`store.py:193-216`), so the client write must be bounded and fail-open
exactly as the current implementation already is.

#### What this costs

Today a dead KV server degrades gracefully: `session_store` keeps persisting and OTel
attribution keeps working, because both bypass it. Under an API-only rule they stop — the blast
radius of the server being down grows from "no incident triage" to "no session persistence and
no attribution either."

That is an acceptable trade only if the server stops being the unsupervised background job it is
today. **The supervision, probe, and leader-ownership work is a prerequisite for this seam, not a
parallel workstream** — which is why section 6 puts phase 3 ahead of the port, and why phases
S1–S3 of [`agent-process-supervisor.md`](agent-process-supervisor.md) come before that. It is
also why the cache above has to be write-through: a fail-open miss is an acceptable answer to the
server being down, but it must not be the normal path.

### Seam B — API and trust zones

Today every route sits in one undifferentiated zone. Split by what a caller can cause:

| Zone | Routes                                                                    | Auth                                   |
| ---- | ------------------------------------------------------------------------- | -------------------------------------- |
| 0    | `GET /healthz`                                                            | none; no data in the response          |
| 1    | `GET /v1/sessions/{id}/metadata`, `GET /v1/incidents/by-thread`           | bearer, read scope                     |
| 2    | `POST /v1/incidents`, `PATCH /v1/sessions/{id}/routing`                   | bearer, write scope                    |
| 3    | `POST /v1/sessions`, `POST /v1/sessions/{id}/messages`, `POST /v1/alerts` | bearer, **separate ingest credential** |

Zone 3 is everything that can cause a chat post or a model turn, so it gets its own credential
and its own rate limit. Removing `GET /v1/sessions` (the list route) closes S5; no consumer uses
it — but it is a published route in `gchat-session-metadata-data-flow.md:94-97`, so removing it
is a change to a documented API and lands in section 7's docs list rather than passing silently.

**Routing fields are validated on the way out, not trusted because they were stored** (S7).
`send_notification` builds `target = f"{platform}:{chat_id}:{thread_id}"` from whatever the store
returns (`platform_mcp_server.py:543`), which turns metadata-write access into control of where
the agent's reports go. Two changes: `PATCH /v1/sessions/{id}/routing` moves behind the zone 3
ingest credential rather than a general write scope, and `chat_id`/`thread_id` are shape-checked
against the platform's format before a target is assembled from them.

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
against the gateway — `_run_turn_via_api` at `agentplugins/pubsub-platform/files/platforms/pubsub/adapter.py:893-950`
creates a session, works around the API's 409-and-duplicate-title quirks, then posts a prompt,
under a comment reading "Mirrors what `agents/platform/scripts/session_kv_server.py` does for
event-watcher alerts." Two hand-rolled copies of one flow, each with its own idempotency
workaround, is the argument for making it a primitive.

`POST /v1/alerts` remains, but shrinks to an alert-shaped facade over the primitive: it renders
the event (Seam C), posts the initial chat message, then calls session creation with the
`incident-triage` skill. The watcher stays out of the business of emoji and PDB message
cleanup.

**The operator mints the tokens; the user does not supply them.** `platform-agent-secrets` is
referenced by `SecretKeyRef` (`platformagent_manifests.go:1670-1697`), which means it is created
outside the operator — by Terraform, by the installer, or by hand. Adding required keys to it
would make every existing installation fail to start on upgrade with `CreateContainerConfigError`
until someone edited a Secret, and would leave rotation with no mechanism at all. Instead the
operator reconciles its own `<agent>-session-kv-tokens` Secret: random 32-byte values, one per
caller identity, created if absent and never rewritten. Rotation is deleting it, and the config
hash already in `buildDeployment` restarts the pod that consumes it. A caller identity whose
token is empty is rejected on zones 1–3 — fail closed — but the server logs the missing identity
loudly at startup and keeps zone 0 open, so the failure is diagnosable from the pod's own logs
rather than from a wall of 401s.

`SessionManager.verify_delegation_headers` (`session_manager.py:187-235`) is already an
implemented and tested HMAC verifier with timestamp-skew checking — agent-side callers should
reuse it rather than grow a second scheme; the Go watcher gets a plain bearer token, since it
would otherwise have to reimplement the canonicalization.

Because every caller ends up co-located (section 4.3), the bind address changes from `0.0.0.0`
to `127.0.0.1`. That is a better answer to S1 than a NetworkPolicy would be — the port stops
being reachable from outside the pod at all, rather than being reachable and then filtered — and
it needs no new Kubernetes object. It does not replace the tokens: the pod's other containers
are still on the far side of no boundary at all (S1, S4, S8).

### Seam C — rendering, and triage as a skill

`render.py` takes the four pure formatting functions unchanged; they are already the
best-tested part of the file (`test_session_kv_server.py:22-70`) and only need moving.

**The triage prompt does not move to a data file owned by `session_kv`. It becomes a skill.**
`_build_agent_query` (L248-284) is currently 37 lines of Python string that encode the analysis
instruction, the report format, the console links, the human call-to-action, the follow-up
GitOps procedure, and the authorization to execute it. None of that is transport concern; all of
it is agent capability, and this repository already has a mechanism for agent capability.

So: `agents/platform/skills/incident-triage/SKILL.md`, alongside the eighteen skills already
there. It belongs to the Platform Agent under the placement rule in `AGENTS.md` — it performs
GitOps writes, so it is not a Cluster Agent read-only debugging skill. `session_kv` stops
authoring prompts entirely and instead invokes the skill with structured event data, via the
`skill` field on session creation.

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
   FastAPI app is not.
4. **Other entry points can reuse it.** A kanban card can name `incident-triage` the same way
   the Pub/Sub adapter already passes `--skill` when filing work
   (`adapter.py:861-863`), and the stockout investigator can invoke it directly. The skill
   becomes the single definition of what triage means, rather than one definition per caller.

### Seam D — delivery and orchestration

`notify.py` defines a `Notifier` protocol with `google_chat` and `slack` adapters, absorbing the
platform-specific thread-key derivation currently inline at L189-194, and adding the missing
subprocess timeout (R4). It is also where S9 is closed: `hermes send` is invoked with an explicit
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
  state           TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | dead
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
a task started on app startup and stopped on shutdown, and the supervisor has one KV child rather
than two. It gets **its own executor**, not the shared 40-slot AnyIO threadpool — otherwise R4
returns through the worker instead of through `_start_agent_turn`, which is the failure the
outbox exists to prevent. The bound on that executor is what stops an alert burst from starving
the read paths.

**At-least-once needs step-wise recovery, because `hermes send` is not idempotent.** The
idempotency key makes the _caller's_ retry safe; it does nothing about the worker dying between
posting to chat and recording that it did. So the row advances through named steps —
`post_alert → register_routing → create_session → start_turn` — each committing its result before
the next begins, and recovery resumes at `step` rather than at the beginning. Only the step that
was in flight is ambiguous.

For the one step where ambiguity is expensive, the policy is explicit: **a `post_alert` that may
have landed is not retried.** A duplicate alert in a chat space is worse than a missing thread
binding, and the flow degrades gracefully without one — the triage turn still runs, and
`send_notification` already falls back to the platform's default target when it cannot resolve a
thread (`platform_mcp_server.py:522-546`). The row is marked with `last_error` so the ambiguity is
visible rather than inferred.

Rows that exhaust `max_attempts` become `state = 'dead'` with their last error, and
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
    SV->>KV: start child (one process)
    KV->>KV: open DB with retry until the outgoing leader releases
    W->>Lease: watch; holder == $HOSTNAME ?
    Note over W: followers idle here — block, never exit
    W->>KV: POST /v1/alerts (127.0.0.1, bearer)
    SV->>Lease: renew every 5s
    Note over SV: on loss: stop children, drop label
    Note over W: on loss: stop watching, resume idling
```

Concretely:

- The supervisor starts the KV server as a second child on acquire and stops it on loss, exactly
  as it already does for the gateway. **One child, not two** — the outbox drainer is a task inside
  the API process, for the reason Seam D gives.
- **Delete entrypoint step 5** (`docker-entrypoint.sh:426-431`) and
  `start_session_kv_server()` (`platform_mcp_server.py:573-610`). One owner, no TOCTOU (R7). This
  is only safe once the supervisor runs at every replica count — see
  [`agent-process-supervisor.md`](agent-process-supervisor.md) §3.1 — because at `replicas: 1`
  those two are currently the only things that start the server at all.
- The watcher's `--daemon-url` **stays** `127.0.0.1:8699`, because section 4.3 gates the watcher
  on the same lease. It gains a real token and bounded retry with backoff. Nothing needs to be
  published on the Service, and 8699 never leaves the pod.
- R1's "a dead KV server is invisible" is closed by the supervisor's health endpoint and the
  readiness probe that reads it, specified in
  [`agent-process-supervisor.md`](agent-process-supervisor.md) §3.4. Probing `/healthz` on 8699
  directly would be wrong: followers do not run a KV server, so every follower would be
  permanently NotReady and rollouts at `replicas > 1` would stall.
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
exactly the `replicas > 1` case (`platformagent_manifests.go:73-89`), so turning on
`locking_mode=EXCLUSIVE` while `store.py`, `bridge.py`, and `session_manager.py` still call
`sqlite3.connect` locks them out permanently — and `store.py` holds a long-lived connection
(`store.py:114-128`), so whichever side opens first simply wins. The migration therefore splits
the change in two: the WAL fix lands early, in every opener at once, and exclusivity lands only
after Seam A's port. In the interim the file is `journal_mode=TRUNCATE` with default locking,
which is multi-process safe and drops the `-shm` requirement that makes WAL wrong here.

**The mode is passed in, not sniffed.** Nothing inside the container can see the volume's access
mode. The operator already computes it (`getDefaultStorageConfig`) and sets the environment
variable the server reads.

### 4.2 Acquiring the file at failover

`locking_mode=EXCLUSIVE` means the incoming leader's KV server cannot open the database until the
outgoing one has closed it. [`agent-process-supervisor.md`](agent-process-supervisor.md) §3.5
makes that ordering hold in the absence of a partition, by requiring
`lease_duration > max_poll + child_grace`; it explicitly does not fence a partitioned-but-live
leader, and on a network filesystem a hard-killed holder's locks clear on the file server's
schedule rather than the pod's.

So the server retries. Startup acquires the lock with exponential backoff over a bounded window —
60 s — logging each attempt, and fails only past it. Two constraints tie that number down at both
ends: it must be shorter than the supervisor's restart cap, or a slow handover looks like a
crash-looping child; and the readiness probe's `failureThreshold × periodSeconds` must be longer
than it, or a slow handover restarts the pod. Section 7 checks the inequality rather than trusting
the prose.

### 4.3 The watcher runs on the leader too — so every caller is loopback

`session_store`, `session_otel_bridge`, `incident_context`, and the MCP servers all live in or
below the gateway process, which only the leader runs, and the KV server is a sibling child of
the same supervisor. They are therefore always co-located with the server they call.

One caller is not in that list and has to be accounted for: the **dashboard container**. The
operator gives it `SESSION_KV_DB_PATH` (`platformagent_manifests.go:1949-1952`) and mounts
`system-metadata` at the database directory (`:1998-2002`), and unlike everything above it runs
on every replica — including followers, which have no KV server. Whether `hermes dashboard`
actually opens the file is the open question, and it decides which of two things phase 1 does:
if it does not, the environment variable and the mount are dead configuration and get deleted
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

It is also the right fix for a bug that exists today. Every replica currently runs a watcher,
each with its own in-memory dedup cache — the operator passes no `--dedup-persist`
(`platformagent_manifests.go:2098-2105`) — so at `replicas > 1` one Kubernetes event produces N
injects, N chat posts, and N model turns. Server-side idempotency would collapse those after the
fact; leader-gating prevents them. It also divides the watch load and credential use against
every target cluster by N.

**Gate on the existing Lease; do not hold a second election.** The watcher should watch
`<agent>-leader` and act only while `holder_identity == $HOSTNAME`. Two independent elections
could disagree, and the disagreement is exactly the state to avoid: a watcher running on a pod
with no KV server. This needs no new RBAC — the pod's ServiceAccount already has
`get`/`list`/`watch` on leases in the namespace (`buildPlatformLeaderRole`, L2385-2389), and the
token projected at L1777-1789 declares no custom audience, so it authenticates to the local API
server.

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
incoming leader inherits the outgoing leader's cache. That works — snapshots are written
atomically and per-cluster (`dedupPersistPath`, `dedup.go:243-250`) — but it buys failover
continuity at the price of a second dedup authority, and it is not free the way the same section
claimed. The event-watcher container mounts only `event-watcher-kubeconfig` and
`event-watcher-ksa-token` (`platformagent_manifests.go:2118-2121`); a shared snapshot path needs
a new PVC mount added to it, which is exactly the operator work lease-gating was supposed to
avoid.

**Put the authority in the server instead.** Zone 3 already needs an idempotency index for R2 and
R11. Widen it: `POST /v1/alerts` deduplicates on `(cluster, uid, reason, message-hash)` within a
TTL, and a replay returns the original session rather than executing again. One authority, no
shared file, no new mount — and it collapses three separate problems into one mechanism, because
the same index handles the N-replica duplicate, the failover re-list, and the caller's own retry
across the blackhole.

`--dedup-persist` then becomes what it should have been: an optimisation that saves the wasted
HTTP calls after a handover, worth passing once the mount exists for another reason, and not
something correctness rests on. Its snapshot interval defaults to 30 s (`main.go:675`), so it was
never going to be lossless anyway.

Metrics are not a concern today: `--metrics-addr` defaults to empty and the operator does not
set it, so the watcher's Prometheus registry is dormant. If it is ever enabled, note that the
series would migrate between pods on failover.

**Loopback is still not a trust boundary**, and the design must not treat it as one. Every
container in the pod shares one network namespace — that is the premise of the port-8699
uniqueness argument in `docker-entrypoint.sh:57` — so the dashboard, the credential proxy, and
fluent-bit can all reach `127.0.0.1:8699`. The credential-isolation design exists precisely
because some of those are less trusted than the agent. Loopback callers authenticate with the
same tokens as anyone else; co-location buys locality, not privilege.

---

## 5. Retention

One owner (`retention.py`), and two knobs rather than the two _defaults_ that silently disagree
today: `SESSION_KV_SESSION_RETENTION_DAYS` and `SESSION_KV_INCIDENT_RETENTION_DAYS`, both set
explicitly by the operator. Sessions and incidents get separate windows because a triage report
is useful for longer than a routing row — which is why collapsing to a single knob would have
been the wrong reading of R5. What R5 is about is two _owners_ disagreeing on one table
(`SESSION_KV_CLEANUP_TTL_DAYS` at 14, `SESSION_KV_RETENTION_DAYS` at 7, neither set by the
operator), and that is what one owner fixes.

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

Phases S1–S3 of [`agent-process-supervisor.md`](agent-process-supervisor.md) are a prerequisite
for phase 3 here and are not repeated in this table.

| Phase | Change                                                                                                                                                                                                                                                                                                                      | Risk                                                     |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 1     | Internal only: split into `session_kv/server/`, schema versioning (with the unstamped-database case), indices, `BEGIN IMMEDIATE`, unified retention, subprocess timeouts, leaf-module extraction. **`journal_mode=TRUNCATE` set identically in every opener.** Dashboard env/mount settled either way. Consumers untouched. | Low — pure refactor, existing tests cover it             |
| 2     | **Lease-gate the watcher**, with connection-refused backoff. Fixes N-duplicate alerts on its own, and must precede phase 3 — after that, a follower's watcher has no local listener.                                                                                                                                        | Medium — Go change; failover behaviour needs soaking     |
| 3     | **Survivability.** KV server becomes a supervised child; delete entrypoint step 5 and the MCP launcher.                                                                                                                                                                                                                     | Medium — entrypoint + operator, loopback-only throughout |
| 4     | Auth and trust zones; operator-minted tokens; generalise `POST /v1/sessions` (idempotency key, prompt, skill) and widen its index to cover event dedup; publish `client.py`; old routes kept as authenticated deprecated aliases.                                                                                           | Medium — watcher must ship a token in lockstep           |
| 5     | **Port the three direct openers to `client.py`**, with the write-through cache and fail-open miss behaviour.                                                                                                                                                                                                                | Medium — only now is this safe                           |
| 6     | **`locking_mode=EXCLUSIVE` on RWX**, with the startup lock retry. Only now is the server the last opener.                                                                                                                                                                                                                   | Medium — first change that can fail at failover          |
| 7     | Add the `incident-triage` skill; shrink `_build_agent_query` to an invocation; extract `render.py`, `notify.py`, `triage.py`.                                                                                                                                                                                               | Medium — changes agent-visible prompt text               |
| 8     | Outbox replaces `BackgroundTasks`: in-process drainer, step-wise recovery, dead-letter, `GET /v1/outbox/stats`.                                                                                                                                                                                                             | Medium                                                   |
| 9     | Delete the deprecated aliases. Drop `SESSION_KV_DB_PATH` and the `system-metadata` mount from every container that no longer opens the file.                                                                                                                                                                                | Low — but see below                                      |

Phase 2 is what removes the need for a Service-exposed 8699 and the cross-pod hop that came
with it; an earlier draft of this design had that as a high-risk phase touching the operator and
the Go sidecar together, and lease-gating the watcher deletes it outright. It is worth shipping
early and on its own, because it is independently valuable — the duplicate-alert bug it fixes is
live today at `replicas > 1` — and because its failover behaviour is the one thing here that
needs observation rather than a test. It moved ahead of the survivability phase for a second
reason: phase 3 stops every container from starting its own KV server, and a watcher on a pod
without one has to already know how to wait.

Phase 9 is where the boundary becomes verifiable rather than aspirational. Today the operator
sets `SESSION_KV_DB_PATH` in the shared env block (`platformagent_manifests.go:1188-1191`) and
again for the dashboard (L1949-1952), and mounts `system-metadata` into multiple containers
(L1551, L1999). Once nothing but the server opens the file, all of that can be removed from
every container except the one running it — and the fact that it _can_ be removed is the proof
that no other component is still reaching for SQLite.

---

## 7. Verification

**Unit.** Extend `agents/platform/scripts/test_session_kv_server.py` into per-module tests. The
formatting tests move to `test_render.py` unchanged — they should keep passing byte-for-byte,
which is the check that Seam C was a pure move. New cases: idempotency-key replay returns
`duplicate` without a second chat post; unauthenticated calls to each zone are rejected; a
`session_id` containing `../` is rejected rather than interpolated; concurrent
`register_routing` and `store.write` on one session leave both fields present.

**Boundary.** The API-only rule needs a test, or it decays the first time someone needs a value
in a hurry. Add a check that no module outside `session_kv/server/` imports `sqlite3` against
this database, and that `DEFAULT_SESSION_KV_DB_PATH` survives in exactly one place — it is
currently copy-pasted into four (`store.py:11`, `bridge.py:10`, `session_manager.py:10`,
`platform_mcp_server.py:20`). Cheap to write, and it is the only thing that keeps the seam real.

**Client.** Cache behaviour is the risk Seam A introduces, so test it directly: **a write
followed immediately by a span produces an enriched span with no HTTP request at all** — the
write-through property, and the one that decides whether this is a port or a regression; a miss
emits a span without session attributes rather than blocking; a repeated miss for an unknown ID
does not issue a request per span; a write clears a negative entry for the same ID; an entry
cached without `thread_id` is re-fetched and an entry with one is not; the cache evicts under its
bound; the server being down degrades every caller to fail-open rather than raising into a
gateway hook.

**Schema and locking.** A fixture database created by the old `store.py` DDL — tables present,
`schema_version` absent — migrates to head rather than failing or re-creating. And the two
timing constraints of section 4.2 are asserted in code, not prose: the lock-retry window is
shorter than the supervisor's restart cap and shorter than
`failureThreshold × periodSeconds` on the readiness probe.

**Go.** `injector_test.go` already asserts the headers are sent; add the server-side counterpart
so the assertion means something, plus retry-across-503 coverage.

**Skill.** `incident-triage` gets the same treatment as the other eighteen: a `SKILL.md`, and a
pass from the repository's `review-skill-quality` skill. The behavioural check is that a triage
run started through `POST /v1/sessions` with `"skill": "incident-triage"` produces the same
report shape the Python string produced — that is the evidence Seam C was a move rather than a
rewrite.

**Operator.** The readiness probe and the `Args` change belong to
[`agent-process-supervisor.md`](agent-process-supervisor.md) §6. What this design adds to
`platformagent_manifests_test.go` and the golden files in
`k8s-operator/internal/testing/testdata/platform/expected/` is the retention and journal-mode
environment, the minted-token Secret, and — whichever way section 4.3 resolves it — an assertion
pinning the dashboard's `SESSION_KV_DB_PATH` and `system-metadata` mount as present-and-used or
absent. Note that the golden files do **not** need a Service port or NetworkPolicy for 8699,
because lease-gating the watcher keeps all traffic on loopback. `tests/test_docker_entrypoint.py`
and `deploy/shared/entrypoint_gate_check.sh` both assert on step 5 and must be updated when it is
deleted — `entrypoint_gate_check.sh:280-291` specifically asserts that port 8699 is released.

**End-to-end**, on the e2e cluster at `replicas: 2`:

```bash
# 1. Exactly one leader; the KV server and the watcher are both on it and nowhere else.
kubectl -n kubeagents-system get pods -l kubeagents.io/is-leader=true
#    On a NON-leader pod, 8699 has no listener and the watcher is idle, not restarting:
kubectl -n kubeagents-system get pod <follower> \
  -o jsonpath='{.status.containerStatuses[?(@.name=="event-watcher")].restartCount}'

# 2. The leader answers on loopback, and unauthenticated calls do not.
kubectl -n kubeagents-system exec <leader> -c platform-agent -- \
  curl -sf 127.0.0.1:8699/healthz
kubectl -n kubeagents-system exec <leader> -c platform-agent -- \
  curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:8699/v1/incidents \
  -d '{"chat_id":"x","thread_id":"y","report":"z"}'          # expect 401

# 3. One event, one alert — the check that leader-gating fixed the N-duplicate bug.
#    Trigger a scenario and count chat messages; expect exactly one.

# 4. Kill the leader mid-incident. Expect no duplicate alert after the handover
#    (server-side event dedup) and no lost one (outbox + idempotency). The incoming
#    KV server may log lock-acquisition retries; it must not exceed its window.
kubectl -n kubeagents-system delete pod <leader>

# 5. Outbox drains after that restart. Via the API, not sqlite3: the database is
#    opened exclusively from phase 6 on, so a second process cannot read it.
kubectl -n kubeagents-system exec <pod> -c platform-agent -- \
  curl -sf -H "Authorization: Bearer $READ_TOKEN" 127.0.0.1:8699/v1/outbox/stats
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

- **No external database.** Leader-gated SQLite meets the requirement; Cloud SQL would add a
  dependency and an IAM surface for a store that holds days of routing rows.
- **No request-continuous HA.** The failover blackhole in `leader_elect.py:12-16` is inherited,
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
  side effect of the supervisor owning the child — output goes to inherited stderr and reaches
  fluent-bit, instead of an unbounded file on the PVC — but nothing here exports series. The
  watcher's own registry is dormant for the same reason (`--metrics-addr` unset), so adding one
  here would be the first, and it belongs with that decision rather than inside this
  decomposition.

---

## 9. Where each finding is closed

Section 1 lists twenty-three findings. This is where each one lands, so that none of them can
quietly fail to be addressed.

| Finding                      | Closed by                                                                  | Phase |
| ---------------------------- | -------------------------------------------------------------------------- | ----- |
| S1 no auth, binds `0.0.0.0`  | Seam B trust zones; loopback bind                                          | 4     |
| S2 stored-report injection   | Seam B write scope; Seam C fencing of the prepended report                 | 4, 7  |
| S3 event-data injection      | Seam C — authority moves into the skill, event fields fenced and labelled  | 7     |
| S4 auth theatre              | Seam B — operator-minted per-caller tokens, verified server-side           | 4     |
| S5 PII enumeration           | Seam B — `GET /v1/sessions` removed                                        | 4     |
| S6 path traversal            | Seam B — server mints IDs; `{id}` resolved against the store before use    | 4     |
| S7 metadata trusted on read  | Seam B — routing writes behind the ingest credential, shape-checked on use | 4     |
| S8 free LLM turns            | Seam B — zone 3 credential and rate limit                                  | 4     |
| S9 subprocess env and logs   | Seam D — `notify.py` environment allowlist, redacted failure logging       | 7     |
| S10 no size caps             | Seam C — length cap and control-character stripping                        | 7     |
| R1 unsupervised, unmonitored | `agent-process-supervisor.md` §3.3–3.4                                     | 3     |
| R2 at-most-once triage       | Seam D outbox; idempotency key on session creation                         | 8, 4  |
| R3 multi-writer WAL on NFS   | §4.1 — `TRUNCATE` early, `EXCLUSIVE` after the port                        | 1, 6  |
| R4 threadpool starvation     | Seam D — subprocess timeout, dedicated bounded executor                    | 7, 8  |
| R5 two retention policies    | §5 — one owner, two explicit windows                                       | 1     |
| R6 lost updates              | Seam A — `txn()` with `BEGIN IMMEDIATE`                                    | 1     |
| R7 two launchers, one port   | §4 — entrypoint step 5 and the MCP launcher deleted                        | 3     |
| R8 `init_db()` at import     | Seam A — schema work moves into the app factory's startup                  | 1     |
| R9 missing indices           | Seam A — indices on the timestamp columns; timer-driven GC                 | 1     |
| R10 32-bit session IDs       | Seam B — 128-bit IDs, unique index on the idempotency key                  | 4     |
| R11 no gateway retry         | Seam D — outbox retry with backoff                                         | 8     |
| R12 schema drift in docs     | §7 docs — a documentation fix, not a code one                              | 1     |
| R13 logs and metrics         | Logging via the supervisor's inherited stderr; **metrics declined**, §8    | 3, —  |
