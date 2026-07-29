#!/bin/bash
set -euo pipefail

# Installation script for test-pubsub-extension module.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CONTEXT="${KUBECTL_CONTEXT:-$(kubectl config current-context 2>/dev/null || echo "")}"
NAMESPACE="${HERMES_NAMESPACE:-kubeagents-system}"

if [ -z "$CONTEXT" ]; then
    echo "Error: No kubectl context specified."
    exit 1
fi

echo "============================================================"
echo "Installing Test PubSub Extension"
echo "Kubectl Context: ${CONTEXT}"
echo "Namespace: ${NAMESPACE}"
echo "============================================================"

echo "Applying AgentPlugin CRD..."
kubectl --context="$CONTEXT" apply -f "${REPO_ROOT}/k8s-operator/config/crd/bases/kubeagents.x-k8s.io_agentplugins.yaml"

echo "Building and publishing test-pubsub-extension OCI image..."
IMAGE="gcr.io/tomeklipski-izrhgv/test-pubsub-extension:latest"
gcloud builds submit --tag "$IMAGE" "$SCRIPT_DIR"

echo "Deploying test-pubsub-extension via Helm chart..."
bash "${REPO_ROOT}/scripts/deploy_extension.sh" \
    --extension test-pubsub-extension \
    --context "$CONTEXT" \
    --namespace "$NAMESPACE"

echo "Done! Test PubSub extension installed successfully."
