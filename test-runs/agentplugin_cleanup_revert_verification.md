# Test Run: AgentPlugin Resource Cleanup, Config Limiting, and Path Enforcement

- **Date**: 2026-07-28
- **Target Component**: `k8s-operator` (`internal/controller/platformagent_manifests.go`, `internal/controller/extension_installer.sh`)
- **Target Cluster**: `ka-dev-mgmt` (`gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt`)
- **Deployed Image**: `gcr.io/tomeklipski-izrhgv/k8s-operator:v20260728-090625`

## Implementation Overview

1. **Resource Cleanup & Revert on Removal**:
   - Updated POSIX shell script [`extension_installer.sh`](file:///usr/local/google/home/tomeklipski/d/ka-dev/k8s-operator/internal/controller/extension_installer.sh) to maintain a manifest file `$TARGET_DIR/.installed_extension_files`. On execution, it computes the diff between previously deployed files and currently active extension files in `/etc/agent-extensions-raw/`, deletes files that were removed from extensions (and cleans up empty parent directories), and updates the manifest.
   - Updated [`buildPodTemplateSpec`](file:///usr/local/google/home/tomeklipski/d/ka-dev/k8s-operator/internal/controller/platformagent_manifests.go#L785) to always include the `extension-installer` init container and an optional `extensions-volume` (`Optional: ptr.To(true)`), guaranteeing cleanup executes whenever an extension is deleted and pod restarts.
   - Verified that `renderConfigYAML` functionally renders `config.yaml` from active `AgentPlugin` resources only, ensuring configuration changes revert when an extension is deleted.

2. **Config Field Limitation (`platforms` Subkey Only)**:
   - Updated [`mergeExtensionConfigs`](file:///usr/local/google/home/tomeklipski/d/ka-dev/k8s-operator/internal/controller/platformagent_manifests.go#L1842) to filter top-level keys in `ext.Spec.Config` against `allowedExtensionConfigFields` (`"platforms"` only). Attempts to inject or overwrite `model:`, `terminal:`, `mcp_servers:`, or other top-level keys are ignored.

3. **File Path Limitation (`skills/` and `plugins/` Only)**:
   - Updated [`isValidExtensionFilePath`](file:///usr/local/google/home/tomeklipski/d/ka-dev/k8s-operator/internal/controller/platformagent_manifests.go#L936) to enforce that cleaned file paths must start with `skills/` or `plugins/`. Any relative path outside these directories (e.g. `prompts/`, `platforms/`, `scripts/`, or directory traversal attempts) is rejected with warning logs and omitted from the ConfigMap.

## Testing & Verification Results

### 1. Go Unit Tests
Ran all unit tests in `k8s-operator`:
```bash
cd k8s-operator && go test ./...
```
Output:
```
ok      github.com/gke-labs/kube-agents/k8s-operator/cmd/k8s-event-watcher      (cached)
ok      github.com/gke-labs/kube-agents/k8s-operator/internal/controller        0.180s
ok      github.com/gke-labs/kube-agents/k8s-operator/internal/testing           (cached)
ok      github.com/gke-labs/kube-agents/k8s-operator/internal/webhook           (cached)
```
Status: **PASS** (Included unit test `TestExtensionInstallerScript_Cleanup`, `TestMergeExtensionConfigs_PlatformsSubkeyOnly`, `TestIsValidExtensionFilePath`, and golden file updates).

### 2. Live E2E Cluster Verification on `ka-dev-mgmt`

Automated test script executed: [`scripts/e2e_verify_extension_cleanup.py`](file:///usr/local/google/home/tomeklipski/d/ka-dev/scripts/e2e_verify_extension_cleanup.py).

```bash
python3 scripts/e2e_verify_extension_cleanup.py
```

Test Results:
- **Requirement 2 (Config Limiting)**: Applied `AgentPlugin` with top-level keys `model`, `terminal`, and `platforms.pubsub`. Verified `platform-agent-config` ConfigMap contained `platforms.pubsub.test_key` while `model` and `terminal` keys were ignored (**PASSED**).
- **Requirement 3 (Path Limiting)**: Applied `AgentPlugin` with files under `skills/`, `plugins/`, `prompts/`, `platforms/`, and `../../../../etc/passwd`. Verified `platform-agent-extensions` ConfigMap contained only `skills/` and `plugins/` keys (**PASSED**).
- **Requirement 1 (Resource Cleanup & Revert)**:
  - Applied `AgentPlugin` with custom skill `skills/e2e-cleanup-test-skill/SKILL.md` and config marker `req1_test_marker`.
  - Verified skill file existed on pod filesystem at `/opt/data/skills/e2e-cleanup-test-skill/SKILL.md` and config marker was active in `config.yaml`.
  - Deleted `AgentPlugin`.
  - Verified `config.yaml` was re-rendered without the extension config (reverted).
  - Verified pod init container executed file cleanup and `/opt/data/skills/e2e-cleanup-test-skill/SKILL.md` was removed (**PASSED**).

Summary Output:
```
=== Starting E2E Verification on Cluster ka-dev-mgmt (gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt) ===

=== Testing Requirement 2: Limit Config Fields to 'platforms' Subkey Only ===
PASSED: Requirement 2 config limiting verified successfully!

=== Testing Requirement 3: Limit File Paths to skills/ and plugins/ Only ===
PASSED: Requirement 3 path limiting verified successfully!

=== Testing Requirement 1: Resource Cleanup & Revert on Removal ===
1. Applying AgentPlugin resource...
Verified extension config marker is active in config.yaml.
Verified deployed skill file exists on pod filesystem.

2. Deleting AgentPlugin resource...
Verified extension config changes successfully reverted in config.yaml.
Verified deployed file successfully removed from pod filesystem!
PASSED: Requirement 1 resource cleanup and revert verified successfully!

=======================================================
ALL E2E VERIFICATION TESTS PASSED SUCCESSFULLY 100%!
=======================================================
```
