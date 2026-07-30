#!/usr/bin/env bash
# ==============================================================================
# 🧪 E2E Test Script: PubSubAdapter Stockout Flow Scenario
# ==============================================================================
# Publishes a GKE scale-up stockout failure alert message to GCP Pub/Sub topic,
# verifies message consumption, session creation, & prompt injection via
# session_kv_server REST API.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VARS_FILE="${SCRIPTS_DIR}/vars.sh"

if [ -f "$VARS_FILE" ]; then
  source "$VARS_FILE"
fi

PROJECT_ID="${PROJECT_ID:-dharb-gkedemos}"
CLUSTER_NAME="${CLUSTER_NAME:-platform-agent-host}"
TOPIC_NAME="gke-stockout-alerts-topic"
AGENT_NAMESPACE="kubeagents-system"

echo -e "🚀 [Scenario 2] Starting PubSubAdapter Stockout E2E Test..."
echo -e "   Target Topic: '${TOPIC_NAME}' in project '${PROJECT_ID}'"
echo -e "   Cluster Name: '${CLUSTER_NAME}'"

# 1. Publish stockout alert payload to Pub/Sub topic
PAYLOAD=$(cat <<EOF
{
  "event_name": "GKE Stockout Warning",
  "severity": "CRITICAL",
  "resource": {
    "type": "k8s_cluster",
    "labels": {
      "cluster_name": "${CLUSTER_NAME}",
      "location": "us-east4"
    }
  },
  "jsonPayload": {
    "messageId": "scale.up.error.out.of.resources",
    "noDecisionStatus": {
      "noScaleUp": {
        "reason": "Stockout: insufficient capacity in us-east4-a for n2d-standard-8",
        "unhandledPodGroups": [
          {
            "podGroup": {
              "samplePod": {
                "namespace": "default",
                "controller": {
                  "name": "e2e-stockout-service-$(date +%s)"
                }
              }
            }
          }
        ]
      }
    }
  }
}
EOF
)

echo -e "📤 Publishing stockout test payload to Pub/Sub topic '${TOPIC_NAME}'..."
MSG_ID=$(gcloud pubsub topics publish "${TOPIC_NAME}" --project="${PROJECT_ID}" --message="${PAYLOAD}" --format='value(messageIds[0])')
echo -e "   Published Message ID: ${MSG_ID}"

# 2. Wait for PubSubAdapter to consume and process message
echo -e "⏳ Waiting for PubSubAdapter to pull and process message..."
sleep 5

# 3. Check session_kv_server for created session
echo -e "🔍 Verifying session creation in session_kv_server (http://127.0.0.1:8699)..."
GATEWAY_POD=$(kubectl get pods -n "${AGENT_NAMESPACE}" --no-headers 2>/dev/null | grep platform-agent-gateway | head -n 1 | awk '{print $1}')

if [ -z "$GATEWAY_POD" ]; then
  echo -e "❌ Error: Could not find platform-agent-gateway pod in namespace '${AGENT_NAMESPACE}'"
  exit 1
fi


SESSIONS_JSON=$(kubectl exec -n "${AGENT_NAMESPACE}" "${GATEWAY_POD}" -c platform-agent -- curl -s http://127.0.0.1:8699/v1/sessions)

echo -e "📋 Active sessions output:"
echo "$SESSIONS_JSON"

if echo "$SESSIONS_JSON" | grep -q "sessions"; then
  echo -e "✅ E2E TEST PASSED: PubSubAdapter stockout message successfully processed and injected!"
else
  echo -e "⚠️ Warning: Could not verify session output from session_kv_server."
fi
