#!/usr/bin/env bash
# ==============================================================================
# 🛠️ Local Development: Rebuild & Redeploy Common Alerting Container
# ==============================================================================
# Builds, pushes, and restarts GKE platform-agent & k8s-event-watcher deployments
# using the 'common-alerting' image tag.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

IMAGE_TAG="${1:-common-alerting}"
IMAGE_REPO="us-east4-docker.pkg.dev/dharb-gkedemos/kube-agents/platform-agent"
FULL_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"
NAMESPACE="kubeagents-system"

echo "========================================================"
echo "🚀 Rebuilding & Redeploying Common Alerting Pipeline"
echo "  Repo Root:   ${REPO_ROOT}"
echo "  Image Target: ${FULL_IMAGE}"
echo "  Namespace:    ${NAMESPACE}"
echo "========================================================"

cd "${REPO_ROOT}"

if [ ! -f "tags.env" ]; then
    echo "❌ Error: tags.env not found in ${REPO_ROOT}"
    exit 1
fi

HERMES_AGENT_TAG=$(grep '^HERMES_AGENT_TAG=' tags.env | cut -d'=' -f2)
if [ -z "${HERMES_AGENT_TAG}" ]; then
    echo "❌ Error: HERMES_AGENT_TAG could not be resolved from tags.env"
    exit 1
fi

echo "📦 [1/4] Building Docker container image (${FULL_IMAGE})..."
docker build \
    --build-arg HERMES_AGENT_TAG="${HERMES_AGENT_TAG}" \
    -f deploy/docker/Dockerfile \
    --target platform \
    -t "${FULL_IMAGE}" .

echo "📤 [2/4] Pushing image to Artifact Registry..."
docker push "${FULL_IMAGE}"

echo "🔄 [3/4] Updating deployment container images in cluster..."
kubectl set image deployment/platform-agent-gateway platform-agent="${FULL_IMAGE}" -n "${NAMESPACE}"
kubectl set image deployment/k8s-event-watcher event-watcher="${FULL_IMAGE}" -n "${NAMESPACE}"

echo "♻️ [4/4] Restarting deployments & waiting for rollout status..."
kubectl rollout restart deployment/platform-agent-gateway deployment/k8s-event-watcher -n "${NAMESPACE}"
kubectl rollout status deployment/platform-agent-gateway -n "${NAMESPACE}"
kubectl rollout status deployment/k8s-event-watcher -n "${NAMESPACE}"

echo "✅ Common Alerting pipeline successfully redeployed!"
