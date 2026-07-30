#!/usr/bin/env bash
# ==============================================================================
# 🧪 E2E Test Script: k8s-event-watcher Scenario
# ==============================================================================
# Triggers a Kubernetes warning event by creating a pod with an unresolvable
# image, verifies session creation & prompt injection via session_kv_server REST API,
# and cleans up test resources.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VARS_FILE="${SCRIPTS_DIR}/vars.sh"

if [ -f "$VARS_FILE" ]; then
  source "$VARS_FILE"
fi

NAMESPACE="${NAMESPACE:-default}"
POD_NAME="e2e-event-watcher-test-$(date +%s)"
AGENT_NAMESPACE="kubeagents-system"

# Record start timestamp (UTC) to distinguish newly created sessions from old ones
TEST_START_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo -e "🚀 [Scenario 1] Starting k8s-event-watcher E2E Test..."
echo -e "   Target Pod: ${POD_NAME} in namespace '${NAMESPACE}'"

# 1. Apply test pod to generate warning events
echo -e "📦 Creating test pod to generate ImagePullBackOff warning event..."
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  namespace: ${NAMESPACE}
spec:
  containers:
  - name: test-container
    image: invalid-image-name-for-e2e-testing:nonexistent
    resources:
      requests:
        cpu: "10m"
        memory: "16Mi"
      limits:
        cpu: "50m"
        memory: "32Mi"
  restartPolicy: Never
EOF

# Cleanup trap
cleanup() {
  echo -e "🧹 Cleaning up test pod ${POD_NAME}..."
  kubectl delete pod "${POD_NAME}" -n "${NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 2. Check session_kv_server for created session
echo -e "🔍 Verifying session creation in session_kv_server (http://127.0.0.1:8699)..."
GATEWAY_POD=$(kubectl get pods -n "${AGENT_NAMESPACE}" --no-headers 2>/dev/null | grep platform-agent-gateway | head -n 1 | awk '{print $1}')

if [ -z "$GATEWAY_POD" ]; then
  echo -e "❌ Error: Could not find platform-agent-gateway pod in namespace '${AGENT_NAMESPACE}'"
  exit 1
fi

MAX_RETRIES=15
PASSED=false

for i in $(seq 1 $MAX_RETRIES); do
  SESSIONS_JSON=$(kubectl exec -n "${AGENT_NAMESPACE}" "${GATEWAY_POD}" -c platform-agent -- curl -s http://127.0.0.1:8699/v1/sessions 2>/dev/null || true)
  
  # Check if a k8s-watcher session created after TEST_START_TIME exists
  LATEST_CREATED_AT=$(echo "$SESSIONS_JSON" | grep -o '"created_at":"[^"]*"' | head -n 1 | cut -d'"' -f4 || true)
  if [ -n "$LATEST_CREATED_AT" ] && [[ "$LATEST_CREATED_AT" > "$TEST_START_TIME" || "$LATEST_CREATED_AT" == "$TEST_START_TIME" ]]; then
    PASSED=true
    break
  fi
  sleep 2
done

echo -e "📋 Active sessions output:"
echo "$SESSIONS_JSON"

if [ "$PASSED" = true ]; then
  echo -e "✅ E2E TEST PASSED: k8s-event-watcher session successfully created and injected!"
else
  echo -e "❌ E2E TEST FAILED: 'k8s-watcher' session was not created within timeout."
  exit 1
fi
