# Hermes Sessions & Kubernetes Operator Local E2E Verification Suite

## Overview

This verification suite enables end-to-end testing of the **Hermes Platform Agent sessions**, session REST API gateways, event injection pipelines, **PubSub extension adapters**, and the **Kubernetes Operator (`PlatformAgent` controller)** without deploying to a live GKE cluster or executing cloud provisioning processes.

---

## 🛠️ Verification Suite Architecture

```
                                    +-------------------------------------------------------------+
                                    |                Local E2E Verification Suite                 |
                                    |               (`scripts/run_local_e2e_suite.py`)           |
                                    +------------------------------+------------------------------+
                                                                   |
                                        +--------------------------+--------------------------+
                                        |                                                     |
                                        v                                                     v
                     +------------------------------------+                +------------------------------------+
                     |    Go Operator Verification Suite  |                |   Python Hermes Session Suite      |
                     |  (`k8s-operator/internal/testing`) |                |   (`tests/test_hermes_operator_e2e.py`)|
                     +-----------------+------------------+                +-----------------+------------------+
                                       |                                                     |
               +-----------------------+-----------------------+            +----------------+-----------------------+
               |                                               |            |                                        |
               v                                               v            v                                        v
     +-------------------+                           +--------------------+ +---------------------+        +--------------------+
     |  CRD Reconciler   |                           |  k8s-event-watcher | |  Hermes Mock Server |        | PubSub Extension   |
     |  (Fake API /      |                           |  Incident Injector | |  Session API        |        | Adapter & E2E      |
     |   envtest)        |                           |  Integration Test  | |  (/sessions)        |        | Verification       |
     +-------------------+                           +--------------------+ +---------------------+        +--------------------+
```

---

## 🧪 Implemented Test Components

### 1. Go Operator & Hermes Controller E2E Suite
- **File**: `k8s-operator/internal/testing/hermes_operator_e2e_test.go`
- **Tests**:
  - `TestHermesOperatorReconciliation_E2E`: Reconciles a `PlatformAgent` object containing `HarnessSpec` and `HermesSpec` using the `controller-runtime` fake client. Asserts generation of gateway `StatefulSet`/`Deployment`, `ServiceAccount`, and `ConfigMap` resources with correct security contexts and env configuration.
  - `TestHermesSessionDaemonIntegration_E2E`: Spawns a local HTTP Hermes Daemon mock server (`httptest.Server`). Tests `POST /sessions` creation with `Authorization` Bearer token and `X-Asserted-Caller` headers, followed by `POST /sessions/{id}/events` incident payload injection.

### 2. Go Event Watcher Unit & Integration Suite
- **Directory**: `k8s-operator/cmd/k8s-event-watcher/`
- **Tests**:
  - `TestInjectorCreateSession` & `TestInjectorInject`: Verifies REST communication between event watcher injector and session daemon.
  - `TestFilterAccept`: Verifies event filtering by reason, namespace exclusions, and unhealthy event minimum count thresholds.
  - `TestDedupObserve`: Verifies rolling deduplication window matching.

### 3. Python Hermes Session & Operator E2E Suite
- **File**: `tests/test_hermes_operator_e2e.py`
- **Tests**:
  - `test_session_lifecycle`: Tests full session creation (`POST /sessions`), session lookup (`GET /sessions/{id}`), event injection (`POST /sessions/{id}/events`), and session termination (`DELETE /sessions/{id}`).
  - `test_unauthorized_session_creation`: Asserts HTTP 401 response for missing/invalid bearer tokens.
  - `test_per_incident_session_routing`: Verifies distinct session ID generation per incident.
  - `test_operator_hermes_harness_config`: Validates operator harness spec parsing.

### 4. Python PubSub Extension Adapter & Session Suite
- **Files**: `tests/test_pubsub_adapter.py` & `tests/test_pubsub_e2e.py`
- **Extension Path**: `extensions/pubsub-platform/files/platforms/pubsub/adapter.py`
- **Tests**:
  - `test_process_message_dispatches_event`: Tests `PubSubAdapter` message parsing and Hermes session dispatching.
  - `test_eval_filter`: Tests message filtering by severity and environmental context.
  - `test_validate_message_code`: Verifies programmatic validation rules (skipping failing messages, passing valid messages).
  - `test_hermes_pubsub_session_contents_and_completion`: Tests Hermes session creation, notification delivery, and message lifecycle.

### 5. Python Platform Agent Skills Integrity Suite
- **File**: `tests/test_platform_skills_integrity.py`
- **Tests**:
  - `test_skills_directory_exists` & `test_all_skills_have_valid_skill_md`: Validates all 20 platform agent skills in `agents/platform/skills/` for valid frontmatter and metadata structure.

### 6. Master Local Verification Runner
- **File**: `scripts/run_local_e2e_suite.py`
- Exposes a single command to execute all local E2E test suites with execution timing and status summaries.

---

## 📊 Empirical Test Results

```
=======================================================================
🚀 Running Local Verification Suite (Hermes Sessions & K8s Operator)
=======================================================================

--- Running: Go Operator & Hermes Controller E2E Tests ---
=== RUN   TestHermesOperatorReconciliation_E2E
--- PASS: TestHermesOperatorReconciliation_E2E (0.01s)
=== RUN   TestHermesSessionDaemonIntegration_E2E
--- PASS: TestHermesSessionDaemonIntegration_E2E (0.00s)
PASS
ok  	github.com/gke-labs/kube-agents/k8s-operator/internal/testing	0.335s

--- Running: Go k8s-event-watcher Unit & Integration Tests ---
=== RUN   TestDedupObserve
--- PASS: TestDedupObserve (0.00s)
=== RUN   TestCanonicalReasonMatching
--- PASS: TestCanonicalReasonMatching (0.00s)
=== RUN   TestMessageAwareReasonMatching
--- PASS: TestMessageAwareReasonMatching (0.00s)
=== RUN   TestDispatcherDispatch_NewIncidentAndFollowUp
--- PASS: TestDispatcherDispatch_NewIncidentAndFollowUp (0.00s)
=== RUN   TestFilterAccept
--- PASS: TestFilterAccept (0.00s)
=== RUN   TestInjectorCreateSession
--- PASS: TestInjectorCreateSession (0.00s)
=== RUN   TestInjectorInject
--- PASS: TestInjectorInject (0.00s)
=== RUN   TestToTriageEvent
--- PASS: TestToTriageEvent (0.00s)
PASS
ok  	github.com/gke-labs/kube-agents/k8s-operator/cmd/k8s-event-watcher	0.329s

--- Running: Python Hermes Session & Operator E2E Suite ---
test_per_incident_session_routing (tests.test_hermes_operator_e2e.TestHermesSessionsE2E) ... ok
test_session_lifecycle (tests.test_hermes_operator_e2e.TestHermesSessionsE2E) ... ok
test_unauthorized_session_creation (tests.test_hermes_operator_e2e.TestHermesSessionsE2E) ... ok
test_operator_hermes_harness_config (tests.test_hermes_operator_e2e.TestK8sOperatorManifestGeneration) ... ok
Ran 4 tests in 0.518s
OK

--- Running: Python PubSub Adapter Unit Tests ---
Ran 12 tests in 0.010s
OK

--- Running: Python PubSub E2E Session Tests ---
Ran 2 tests in 0.016s
OK (skipped=1)

--- Running: Python Platform Agent Skills Integrity Tests ---
test_all_skills_have_valid_skill_md (tests.test_platform_skills_integrity.TestPlatformSkillsIntegrity) ... ok
test_skills_directory_exists (tests.test_platform_skills_integrity.TestPlatformSkillsIntegrity) ... ok
Ran 2 tests in 0.002s
OK

=======================================================================
📊 Verification Suite Summary
=======================================================================
✅ Go Operator & Hermes Controller E2E Tests     [PASSED] (0.335s)
✅ Go k8s-event-watcher Unit & Integration Tests [PASSED] (0.329s)
✅ Python Hermes Session & Operator E2E Suite    [PASSED] (0.606s)
✅ Python PubSub Adapter Unit Tests              [PASSED] (0.094s)
✅ Python PubSub E2E Session Tests               [PASSED] (0.100s)
✅ Python Platform Agent Skills Integrity Tests  [PASSED] (0.053s)
=======================================================================

🎉 All tests passed cleanly without cluster deployment!
```

---

## 🏃 How to Run Locally

To run the complete verification suite at any time:

```bash
python3 scripts/run_local_e2e_suite.py
```

To run PubSub extension tests specifically:

- **PubSub Adapter Unit Tests**:
  ```bash
  python3 -m unittest -v tests/test_pubsub_adapter.py
  ```

- **PubSub Session E2E Tests**:
  ```bash
  python3 -m unittest -v tests/test_pubsub_e2e.py
  ```

- **PubSub Extension Empirical Validation Script**:
  ```bash
  python3 scripts/validate_pubsub_extension.py
  ```
