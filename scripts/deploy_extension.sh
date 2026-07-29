#!/bin/bash
set -euo pipefail

# Helper script to deploy AgentPlugin modules using Helm charts.
# Respects User Rule 15: Always uses a dedicated kubectl context.

EXTENSION=""
CONTEXT="${KUBECTL_CONTEXT:-}"
NAMESPACE="kubeagents-system"
RELEASE_NAME=""
HELM_EXTRA_ARGS=()

usage() {
    echo "Usage: $0 --extension <extension-name> [--context <kubectl-context>] [--namespace <namespace>] [--release-name <name>] [helm set args...]"
    echo "Example: $0 --extension gke-stockout-handler --context kind-kind --namespace kubeagents-system --set clusterName=ka-production"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --extension)
            EXTENSION="$2"
            shift 2
            ;;
        --context)
            CONTEXT="$2"
            shift 2
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --release-name)
            RELEASE_NAME="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            HELM_EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

if [ -z "$EXTENSION" ]; then
    echo "Error: --extension is required."
    usage
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHART_DIR="${REPO_ROOT}/extensions/${EXTENSION}"

if [ ! -d "$CHART_DIR" ]; then
    echo "Error: Helm chart directory not found at ${CHART_DIR}"
    exit 1
fi

if [ -z "$RELEASE_NAME" ]; then
    RELEASE_NAME="$EXTENSION"
fi

if [ -z "$CONTEXT" ]; then
    CONTEXT="$(kubectl config current-context 2>/dev/null || echo "")"
fi

if [ -z "$CONTEXT" ]; then
    echo "Error: No kubectl context specified via --context or KUBECTL_CONTEXT environment variable."
    exit 1
fi

echo "============================================================"
echo "Deploying AgentPlugin module: ${EXTENSION}"
echo "Helm Release: ${RELEASE_NAME}"
echo "Kubectl Context: ${CONTEXT}"
echo "Namespace: ${NAMESPACE}"
echo "Chart Path: ${CHART_DIR}"
echo "============================================================"

# Verify CRD is installed
echo "Verifying AgentPlugin CRD presence in cluster..."
if ! kubectl --context="$CONTEXT" get crd agentplugins.kubeagents.x-k8s.io >/dev/null 2>&1; then
    echo "AgentPlugin CRD not found. Applying operator CRDs..."
    kubectl --context="$CONTEXT" apply -f "${REPO_ROOT}/k8s-operator/config/crd/bases/kubeagents.x-k8s.io_agentplugins.yaml"
fi

# Run Helm upgrade --install
echo "Executing helm upgrade --install..."
helm upgrade --install "$RELEASE_NAME" "$CHART_DIR" \
    --kube-context "$CONTEXT" \
    --namespace "$NAMESPACE" \
    --create-namespace \
    "${HELM_EXTRA_ARGS[@]}"

echo "Verifying AgentPlugin status..."
kubectl --context="$CONTEXT" get agentplugins -n "$NAMESPACE"

echo "Successfully deployed ${EXTENSION} extension module!"
