# Agent Process Supervisor

> **STATUS — draft; not implemented.** Nothing here ships today. Section 1 describes the
> launch path as it currently exists; sections 3 onward are the proposal.

**Scope:** how long-lived processes inside the `platform-agent` container are started,
supervised, and stopped — at every replica count — and how their health reaches the kubelet.
**Owns:** the container's process model, `leader_elect.py`'s two modes, the per-child restart
policy, the supervisor health endpoint and the readiness probe that reads it, the lease timing
parameters, and what the Lease does and does not fence.
**Does not own:** what any individual child process does. The Session KV server is specified in
[`session-kv-decomposition.md`](session-kv-decomposition.md), which depends on this design for
its launch path and cites it rather than restating it.

**Why this is a separate design.** The Session KV decomposition needs a supervised, single-owner
KV server, and reached for `leader_elect.py` to get one. But the gap it has to cross —
`leader_elect.py` does not run at all at the default replica count — is not a `session_kv`
problem. It is the reason the gateway container has no probes, the reason there are three things
that can start a background process and nothing that owns one, and the reason a child crash is
a pod restart. Fixing it under the KV server's name would leave the next component that needs a
supervised sibling to rediscover the same ground.

---

## 1. What exists today

### 1.1 Two launch paths, chosen by replica count

The image `ENTRYPOINT` is `deploy/shared/docker-entrypoint.sh`, which builds the shared tree and
ends in `exec "$@"` (`:442-443`). What `"$@"` is depends on the replica count:

| Replicas | Gateway container `Args`        | What supervises `hermes gateway run` |
| -------- | ------------------------------- | ------------------------------------ |
| 1        | unset — the image `CMD`         | nothing; it is PID 1's exec target   |
| > 1      | `python3 $HOME/leader_elect.py` | `leader_elect.py`                    |

The operator sets `Args` only in the `replicas > 1` branch
(`k8s-operator/internal/controller/platformagent_manifests.go:1876-1880`), and sets
`LEADER_ELECTION_LEASE_NAME` / `LEADER_ELECTION_NAMESPACE` in the same branch (`:1315-1330`).

`leader_elect.py` has a third path of its own: with either variable unset it calls
`os.execvp("hermes", ["hermes", "gateway", "run"])` and replaces itself (`:60-61`). So even when
the script is the entrypoint's exec target, it supervises nothing unless the election is
configured.

### 1.2 Three launchers, none of them the supervisor

Nothing that is not the gateway is started by whatever is supervising the gateway:

| Process              | Started by                                            | Supervised by |
| -------------------- | ----------------------------------------------------- | ------------- |
| `hermes gateway run` | `leader_elect.py:138`, or the entrypoint's exec       | see above     |
| Session KV server    | `docker-entrypoint.sh:426-429`, with `&`              | nothing       |
| Session KV server    | `platform_mcp_server.py:573-610`, if the port is free | nothing       |

The last two race: the MCP launcher probes port 8699 and spawns if the connect fails, which is a
TOCTOU against the entrypoint's background start. The loser exits with `EADDRINUSE` into a log
file on the PVC.

### 1.3 One supervision idiom

`leader_elect.py` handles exactly one child and has one response to its death: print the exit
code and `sys.exit` it, so the kubelet restarts the pod (`:139-141`). On lease loss it drops the
`kubeagents.io/is-leader` label, terminates the child, waits 10 s, and kills (`:142-153`); the
`SIGTERM` path does the same and then releases the lease (`:25-55`).

### 1.4 No health signal from the gateway container

The `platform-agent` container declares no probe of any kind (`platformagent_manifests.go:1915-1937`).
The only readiness probe in the pod belongs to the credential proxy (`:1629-1635`). Nothing the
gateway container runs — the gateway itself, the KV server's `/healthz` — is ever asked whether
it is alive.

At `replicas > 1` the Service selector gains `kubeagents.io/is-leader=true` (`:2335-2338`), so
followers are already excluded from endpoints by label. Readiness today therefore changes
nothing about routing, and its absence costs only visibility.

### 1.5 The timing parameters

| Parameter                    | Value                                      | Where                                |
| ---------------------------- | ------------------------------------------ | ------------------------------------ |
| `lease_duration_seconds`     | 15 s                                       | `leader_elect.py:70`                 |
| poll interval                | 5 s + U(0,2)                               | `:71`, `:156`                        |
| child termination grace      | 10 s                                       | `:36`, `:148`                        |
| Deployment strategy, `n > 1` | RollingUpdate, 25% surge / 25% unavailable | `manifest_helpers.go:66`, `:283-290` |

---

## 2. Problems

| ID  | Severity | Problem                                                                                                                                                                                                                                                                                                                                              |
| --- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1  | High     | **The default replica count has no supervisor.** At `replicas: 1` — the default (`manifest_helpers.go:266`) — `leader_elect.py` is not in the picture. Any sibling process moved under it disappears from the majority deployment.                                                                                                                   |
| P2  | High     | **The unconfigured path cannot supervise.** `os.execvp` at `:61` replaces the process, so there is no code left to start a second child even when the script is the exec target.                                                                                                                                                                     |
| P3  | Medium   | **Child death is pod death.** One idiom for one child. Three children under that rule means any one of them flapping restarts the gateway and drops the lease.                                                                                                                                                                                       |
| P4  | Medium   | **Child health is invisible, and a naive probe makes it worse.** Adding a readiness probe that targets a leader-only child takes every follower out of Ready. With 2 replicas, 25% `maxUnavailable` rounds down to 0, so a rollout that can never reach 2 available pods stalls to `progressDeadlineSeconds`. Today's no-probe state at least rolls. |
| P5  | Medium   | **The lease can be reacquired before the outgoing leader has let go.** Worst-case detection plus shutdown is 7 s of poll plus 10 s of grace = 17 s, against a 15 s lease. Any resource a child holds exclusively — a file lock, a port on a shared volume — can still be held when the next leader starts its own.                                   |
| P6  | Low      | **A Lease does not fence.** A leader that is partitioned from the API server but alive keeps running its children until its own next poll fails; `holder_identity` says nothing about what is still executing.                                                                                                                                       |

---

## 3. Design

### 3.1 One supervisor, two modes, every replica count

`leader_elect.py` becomes a supervisor with an explicit mode, and the operator makes it the
gateway container's `Args` unconditionally.

```
mode = elected  if LEADER_ELECTION_LEASE_NAME and LEADER_ELECTION_NAMESPACE else solo
```

- **`solo`** — behave as a permanent leader. Start the children, supervise them, never contact
  the API server, never label the pod. This replaces the `os.execvp` at `:61`; the reason to
  supervise rather than exec is that there is more than one child to start, and that is true
  independent of how many replicas there are.
- **`elected`** — today's loop, unchanged in structure: acquire, label, start children, renew,
  and on loss drop the label and stop them.

Making the script the exec target at every replica count is what collapses 1.1's table to one
row. It has one knock-on: the entrypoint's shared-state auto-detection looks for a bare `gateway`
argument (`platformagent_manifests.go:59-65`), and the gateway container's argv only carries one
at a single replica today. The operator already names the owner explicitly with
`AGENT_SHARED_STATE_SETUP=owner`, so nothing changes in behaviour — but the comment explaining
why auto-detection is not relied on gets simpler, and should be updated rather than left
describing a case that no longer exists.

### 3.2 The child table

| Child                | Start order | Stop order | Notes                                                              |
| -------------------- | ----------- | ---------- | ------------------------------------------------------------------ |
| Session KV server    | 1           | 2          | started first, stopped last: the gateway's plugins are its clients |
| `hermes gateway run` | 2           | 1          |                                                                    |

The ordering is deliberate rather than incidental. The plugins inside the gateway fail open when
the KV server is absent, so a slow start costs attribution rather than availability — but the
dependency runs in that direction and the start order should say so.

Children write to inherited stdout/stderr rather than to a file on the PVC, so their output
reaches fluent-bit like everything else and nothing grows unbounded on the volume.

### 3.3 Restart policy

Per child, not per pod:

- On exit, restart with exponential backoff (1 s doubling to 30 s).
- Count restarts in a sliding window. Past the cap — **5 restarts in 5 minutes** — the supervisor
  gives up on that child and exits, so the kubelet restarts the pod and the lease is released.
- A child that has never started successfully is treated the same way, with the same cap. This
  matters for the KV server, which may legitimately need to wait out a departing leader's file
  lock; the length of that wait belongs to
  [`session-kv-decomposition.md`](session-kv-decomposition.md) and must fit inside the cap.
- On lease loss, stop all children (reverse start order) before returning to the watch loop.
  Termination keeps today's 10 s grace and `SIGKILL` fallback.

### 3.4 Health endpoint and readiness

The supervisor serves `GET /healthz` on `127.0.0.1:8700`, and it is the only thing the readiness
probe consults:

| Pod state                                   | Response                                            |
| ------------------------------------------- | --------------------------------------------------- |
| follower (elected mode, not the holder)     | `200 {"role": "follower", "children": []}`          |
| leader or solo, every child running         | `200 {"role": "leader"\|"solo", "children": [...]}` |
| leader or solo, a child down or backing off | `503 {"role": …, "children": [… "state": "down"]}`  |

A follower answering `200` is the point: it keeps every replica Ready, so the rollout arithmetic
in P4 works, while a leader with a dead child still goes NotReady and leaves the endpoint list.
Because the Service already selects on the leader label, NotReady on a leader is what actually
removes the only endpoint there is — which is the visibility that 1.4 lacks.

```yaml
readinessProbe:
  httpGet: { path: /healthz, port: 8700 }
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 6
```

`failureThreshold × periodSeconds` must exceed the longest legitimate child start, or a slow
start at failover becomes a pod restart. That is the constraint the KV server's lock-acquisition
window has to be chosen against, in both directions.

No liveness probe. A supervisor that has given up already exits (3.3), which is the same outcome
with fewer ways to be wrong.

### 3.5 Lease timing

State the inequality and pick parameters that satisfy it:

```
lease_duration_seconds  >  max_poll_interval + child_shutdown_grace
```

Today that reads `15 > 7 + 10`, which is false, and P5 is the consequence. The proposal:

| Parameter                | Today        | Proposed  |
| ------------------------ | ------------ | --------- |
| `lease_duration_seconds` | 15 s         | **30 s**  |
| poll interval            | 5 s + U(0,2) | unchanged |
| child termination grace  | 10 s         | unchanged |

`30 > 7 + 10` holds with margin. The cost is a longer failover blackhole — the window in which
the Service has zero ready endpoints grows by up to 15 s — which is a real regression in
availability bought for a real guarantee about exclusively-held resources. It is the right trade
only because the blackhole already exists and is already documented as inherited
(`leader_elect.py:12-16`); consumers must retry across it either way.

The guarantee this buys is narrow and should be stated as such: **in the absence of a partition,
the outgoing leader has stopped its children before any other pod can acquire the lease.**

### 3.6 What the Lease does not do

It does not fence. A leader partitioned from the API server keeps running until its own next poll
fails — and today's loop does self-terminate in that case, because a non-404 `ApiException`
leaves `holder` as `None` and falls into the loss branch (`:111-153`) — but "eventually
self-terminates" is not the same as "cannot still be writing". Nothing about the Lease prevents
two processes from both believing they are the leader for a bounded window.

Consequences for anything a child owns exclusively:

- It must be safe for the incoming instance to find the resource still held, and to wait.
- It must be safe for the same work to be attempted twice — idempotency keys, not locks, are what
  make the overlap survivable.

Callers that need continuity across the window retry; the server deduplicates. This design does
not attempt more, and 6 records why.

---

## 4. Operator changes

- Set the gateway container's `Args` to the supervisor at **every** replica count
  (`platformagent_manifests.go:1876-1880`).
- Add the readiness probe of 3.4 to the `platform-agent` container (`:1915-1937`).
- Raise `lease_duration_seconds` to 30 s in the embedded script (`:2517`).
- Update the `AGENT_SHARED_STATE_SETUP` comment at `:59-65`, whose argv-fallback reasoning is
  written around the single-replica case that 3.1 removes.
- Golden files in `k8s-operator/internal/testing/testdata/platform/expected/` gain the probe and,
  at a single replica, the `Args` they currently omit.

---

## 5. Migration

| Phase | Change                                                                                                                                                       | Risk                                                                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| S1    | Supervisor modes and the child table, with the gateway as the only child. Operator sets `Args` unconditionally. Behaviour-preserving at both replica counts. | Low — the single-replica path gains a parent process and nothing else |
| S2    | Per-child restart policy and the health endpoint; readiness probe on the gateway container.                                                                  | Medium — first probe on this container; watch rollouts                |
| S3    | Lease duration to 30 s.                                                                                                                                      | Low — longer blackhole, no new failure mode                           |
| S4    | Second child adopted (the Session KV server), and entrypoint step 5 plus the MCP launcher deleted. Owned by `session-kv-decomposition.md` phase 3.           | Medium — the entrypoint gate check asserts on step 5                  |

S1–S3 are independently shippable and are worth shipping before anything needs them. S4 is where
this design and the KV decomposition meet.

---

## 6. Verification

**Unit.** `leader_elect.py` has no tests today. Add: mode selection from the environment; solo
mode starts children and never touches the API client; a child exiting is restarted with backoff;
the restart cap exits the supervisor; lease loss stops children in reverse order; the health
endpoint's three responses.

**Timing.** Assert the inequality in code rather than in prose — a startup check that
`lease_duration_seconds > max_poll_interval + grace` and refuses to start otherwise. It is one
line, and it is the only thing that keeps 3.5 true after someone tunes a constant.

**Operator.** `platformagent_manifests_test.go` for `Args` at a single replica and the probe on
both; the golden files above.

**Entrypoint.** `deploy/shared/entrypoint_gate_check.sh:277-292` asserts that port 8699 is
released after each case, and `tests/test_docker_entrypoint.py` asserts on step 5. Both change at
S4, not before. The site's `deploy/docker-images.md:76` describes step 5 and goes stale then too.

**End-to-end**, at `replicas: 2`:

```bash
# A follower is Ready even though it runs no children.
kubectl -n kubeagents-system get pod <follower> \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'   # expect True

# A rollout completes. This is the check that P4 stayed fixed.
kubectl -n kubeagents-system rollout restart deploy/<agent>-gateway
kubectl -n kubeagents-system rollout status  deploy/<agent>-gateway --timeout=5m

# Killing a child takes the leader out of endpoints without restarting the pod.
kubectl -n kubeagents-system exec <leader> -c platform-agent -- pkill -f 'session_kv'
kubectl -n kubeagents-system get endpoints <agent>

# And the supervisor restarts it, so the pod comes back on its own.
```

At a single replica the check is simply that the container comes up with a supervisor as its main
process and both children running — which is the state the default deployment does not have
today.

---

## 7. Deliberate non-goals

- **No fencing token.** 3.6 is a limitation, not an oversight. Fencing SQLite behind a monotonic
  token means a second store to hold the token, which is the dependency
  `session-kv-decomposition.md` §8 declines for the same reason.
- **No request-continuous HA.** Raising the lease duration makes the blackhole longer, not
  shorter. Closing it needs warm standbys, which is a different design.
- **No supervision of sidecars.** This owns processes inside the `platform-agent` container. The
  event watcher, the credential proxy, and fluent-bit are containers, and the kubelet already
  supervises those.
