#!/bin/bash
set -euo pipefail

HERMES_NAMESPACE="kubeagents-system"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONTEXT="${KUBECTL_CONTEXT:-$(kubectl config current-context 2>/dev/null || echo "")}"

echo "Installing PubSub Platform Adapter..."
bash "${REPO_ROOT}/extensions/pubsub-platform/install.sh" \
    --context "$CONTEXT" \
    --namespace "$HERMES_NAMESPACE"

echo "Done! PubSub Platform Adapter AgentPlugin installed."
