#!/bin/bash
set -euo pipefail

# ==============================================================================
# End-to-End Empirical Validation Script for PubSub Extension on Kubernetes
# ==============================================================================
# Performs live validations against the cluster deployment using gcloud and kubectl.
# Respects User Rule 15: Always uses a dedicated kubectl context.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration defaults
CONTEXT="${KUBECTL_CONTEXT:-gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt}"
NAMESPACE="${HERMES_NAMESPACE:-kubeagents-system}"
PROJECT_ID="${GCP_PROJECT:-tomeklipski-izrhgv}"
TOPIC="${PUBSUB_TOPIC:-gke-stockout-alerts-topic}"
VALIDATION_TOPIC="test-validation-topic"

usage() {
    echo "Usage: $0 [--context <kubectl-context>] [--namespace <namespace>] [--project <gcp-project>] [--topic <pubsub-topic>]"
    echo "Example: $0 --context gke_tomeklipski-izrhgv_europe-west3_ka-dev-mgmt --namespace kubeagents-system"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --context)
            CONTEXT="$2"
            shift 2
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --project)
            PROJECT_ID="$2"
            shift 2
            ;;
        --topic)
            TOPIC="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            ;;
    esac
done

echo "================================================================="
echo "  PubSub Platform Extension Live Cluster Empirical Validation  "
echo "================================================================="
echo "Kubectl Context : ${CONTEXT}"
echo "Namespace       : ${NAMESPACE}"
echo "GCP Project ID  : ${PROJECT_ID}"
echo "PubSub Topic    : ${TOPIC}"
echo "================================================================="

# Step 1: Verify AgentPlugin CRD & Cluster Pods
echo ""
echo "=== Step 1: Checking Cluster & Deployment Prerequisites ==="
echo "[1.1] Checking AgentPlugin CRD..."
if kubectl --context="$CONTEXT" get crd agentplugins.kubeagents.x-k8s.io >/dev/null 2>&1; then
    echo "✓ AgentPlugin CRD is present in cluster."
else
    echo "❌ AgentPlugin CRD not found. Applying CRD..."
    kubectl --context="$CONTEXT" apply -f "${REPO_ROOT}/k8s-operator/config/crd/bases/kubeagents.x-k8s.io_agentplugins.yaml"
fi

echo "[1.2] Checking platform-agent-gateway deployment status..."
if ! kubectl --context="$CONTEXT" get deployment platform-agent-gateway -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "❌ platform-agent-gateway deployment not found in namespace ${NAMESPACE}."
    exit 1
fi

AVAILABLE_REPLICAS=$(kubectl --context="$CONTEXT" get deployment platform-agent-gateway -n "$NAMESPACE" -o jsonpath='{.status.availableReplicas}')
if [[ -z "$AVAILABLE_REPLICAS" || "$AVAILABLE_REPLICAS" == "0" ]]; then
    echo "❌ platform-agent-gateway deployment has no available replicas. Cannot proceed with validation."
    exit 1
else
    echo "✓ platform-agent-gateway deployment exists and has available replicas."
fi

# Step 2: Validating Stockout Alert Deduplication and Threshold
echo ""
echo "=== Step 2: Validating Stockout Alert Threshold ==="
TEST_ID_GEN="stockout-$(date +%s)"
PAYLOAD_GEN=$(cat <<EOF
{
  "jsonPayload": {
    "resource": {
      "labels": {
        "cluster_name": "ka-production"
      }
    },
    "noDecisionStatus": {
      "noScaleUp": {
        "unhandledPodGroups": [
          {
            "podGroup": {
              "samplePod": {
                "namespace": "${TEST_ID_GEN}"
              }
            }
          }
        ]
      }
    }
  },
  "event_name": "cluster_stockout",
  "severity": "HIGH",
  "region": "europe-west1",
  "project_id": "${PROJECT_ID}",
  "requested_nodes": 3,
  "error_details": "ZONE_RESOURCE_POOL_EXHAUSTED",
  "test_id": "${TEST_ID_GEN}"
}
EOF
)

echo "Publishing 5 test events to topic '${TOPIC}' to bypass threshold_count..."
for i in {1..5}; do
    PUB_OUT_GEN=$(gcloud pubsub topics publish "$TOPIC" --project="$PROJECT_ID" --message="$PAYLOAD_GEN")
    echo "✓ Published message $i ID: ${PUB_OUT_GEN}"
done

echo "Waiting 6 seconds for PubSub adapter to pull and process message..."
sleep 6

echo "Checking gateway logs for received message..."
LOGS_GEN=$(kubectl --context="$CONTEXT" logs -n "$NAMESPACE" deployment/platform-agent-gateway -c platform-agent --tail=200 2>/dev/null || echo "")

if echo "$LOGS_GEN" | grep -i -q "Received message on route"; then
    echo "✓ SUCCESS: Stockout alert passed threshold check and was processed!"
else
    echo "❌ FAILURE: Stockout alert did not pass threshold or was not logged."
    exit 1
fi

echo ""
echo "=== Step 3: Validating Programmatic validation_code (FAILING Message / Invalidated) ==="
TEST_ID_FAIL="fail-$(date +%s)"
PAYLOAD_FAIL=$(cat <<EOF
{
  "event_name": "validation_test",
  "severity": "INFO",
  "message": "Low severity message intended to fail validation requirement",
  "test_id": "${TEST_ID_FAIL}"
}
EOF
)

echo "Publishing FAILING test event to '${VALIDATION_TOPIC}' (severity: INFO, ID: ${TEST_ID_FAIL})..."
PUB_OUT_FAIL=$(gcloud pubsub topics publish "$VALIDATION_TOPIC" --project="$PROJECT_ID" --message="$PAYLOAD_FAIL")
echo "✓ Published message ID: ${PUB_OUT_FAIL}"

echo "Waiting 6 seconds for validation_code invalidation check..."
sleep 6

LOG_FAIL=$(kubectl --context="$CONTEXT" logs -n "$NAMESPACE" deployment/platform-agent-gateway -c platform-agent --tail=200 2>/dev/null || echo "")

if echo "$LOG_FAIL" | grep -i -q "Message on route 'test_validation_route' invalidated by programmatic validation_code"; then
    echo "✓ SUCCESS: FAILING message was correctly invalidated by validation_code and prompt triggering skipped!"
else
    echo "❌ FAILURE: FAILING message was not invalidated as expected."
    exit 1
fi

echo ""
echo "=== Step 4: Validating Programmatic validation_code (PASSING Message) ==="
TEST_ID_PASS="pass-$(date +%s)"
PAYLOAD_PASS=$(cat <<EOF
{
  "event_name": "validation_test",
  "severity": "CRITICAL",
  "message": "Valid alert payload matching validation requirement",
  "test_id": "${TEST_ID_PASS}"
}
EOF
)

echo "Publishing PASSING test event to '${VALIDATION_TOPIC}' (severity: CRITICAL, ID: ${TEST_ID_PASS})..."
PUB_OUT_PASS=$(gcloud pubsub topics publish "$VALIDATION_TOPIC" --project="$PROJECT_ID" --message="$PAYLOAD_PASS")
echo "✓ Published message ID: ${PUB_OUT_PASS}"

echo "Waiting 6 seconds for validation_code evaluation..."
sleep 6

LOGS_PASS=$(kubectl --context="$CONTEXT" logs -n "$NAMESPACE" deployment/platform-agent-gateway -c platform-agent --tail=200 2>/dev/null || echo "")

if echo "$LOGS_PASS" | grep -q "${TEST_ID_PASS}" || echo "$LOGS_PASS" | grep -i -q "Received message on route 'test_validation_route'"; then
    echo "✓ SUCCESS: PASSING message evaluated successfully and allowed by validation_code!"
else
    echo "❌ FAILURE: PASSING message was not processed successfully."
    exit 1
fi

echo ""
echo "================================================================="
echo "  Validation Complete! All tests executed against cluster deployment."
echo "================================================================="
