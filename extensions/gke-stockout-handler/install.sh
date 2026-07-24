#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# GKE Stockout Handler Extension Installation Script
# Ensures GCP APIs, IAM permissions (least privilege), PubSub topic/subscription,
# Log Sink, Platform Agent IAM bindings, and Helm AgentExtension deployment.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve GCP Project ID
PROJECT_ID="${GCP_PROJECT_ID:-${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}}"
if [ -z "$PROJECT_ID" ]; then
    echo "Error: Could not determine GCP Project ID. Please set GCP_PROJECT_ID or PROJECT_ID environment variable."
    exit 1
fi

# Resolve Kubectl Context (User Rule 15: Always use dedicated kubectl context)
CONTEXT="${KUBECTL_CONTEXT:-$(kubectl config current-context 2>/dev/null || echo "")}"
if [ -z "$CONTEXT" ]; then
    echo "Error: No kubectl context found. Set KUBECTL_CONTEXT environment variable."
    exit 1
fi

NAMESPACE="kubeagents-system"
CLUSTER_NAME="${TARGET_CLUSTER_NAME:-ka-production}"
TOPIC="gke-stockout-alerts-topic"
SUBSCRIPTION="gke-stockout-alerts-sub"
SINK_NAME="gke-stockout-alerts-sink"
FILTER='(log_id("test-stockout") OR log_id("container.googleapis.com/cluster-autoscaler-visibility")) AND (jsonPayload.messageId:("scale.up.error.out.of.resources" OR "scale.up.error.quota.exceeded" OR "scale.up.error.ip.space.exhausted" OR "scale.up.no.scale.up") OR jsonPayload.noDecisionStatus.noScaleUp:*)'

echo "============================================================"
echo "Installing GKE Stockout Handler Extension Module"
echo "GCP Project ID:  ${PROJECT_ID}"
echo "Kubectl Context: ${CONTEXT}"
echo "Target Cluster:  ${CLUSTER_NAME}"
echo "Namespace:       ${NAMESPACE}"
echo "PubSub Topic:    ${TOPIC}"
echo "Subscription:    ${SUBSCRIPTION}"
echo "============================================================"

# Step 1: Enable necessary GCP APIs (Least Privilege - specific to this extension)
echo "Step 1: Enabling required GCP APIs for GKE Stockout Handler extension..."
gcloud services enable \
    pubsub.googleapis.com \
    logging.googleapis.com \
    container.googleapis.com \
    --project="$PROJECT_ID" --quiet

# Step 2: Ensure PubSub Topic & Subscription exist beforehand
echo "Step 2: Ensuring PubSub Topic '${TOPIC}' exists..."
if ! gcloud pubsub topics describe "$TOPIC" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub topics create "$TOPIC" --project="$PROJECT_ID" || true
else
    echo "Topic '${TOPIC}' already exists."
fi

echo "Step 2b: Ensuring PubSub Subscription '${SUBSCRIPTION}' exists..."
if ! gcloud pubsub subscriptions describe "$SUBSCRIPTION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$SUBSCRIPTION" --topic="$TOPIC" --project="$PROJECT_ID" || true
else
    echo "Subscription '${SUBSCRIPTION}' already exists."
fi

# Step 3: Ensure GCP Cloud Logging Sink exists & grant least privilege publisher role
echo "Step 3: Ensuring Cloud Logging Sink '${SINK_NAME}' exists..."
TOPIC_PATH="pubsub.googleapis.com/projects/${PROJECT_ID}/topics/${TOPIC}"
if ! gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud logging sinks create "$SINK_NAME" "$TOPIC_PATH" \
        --log-filter="$FILTER" \
        --project="$PROJECT_ID" || true
else
    echo "Updating existing log sink '${SINK_NAME}' filter..."
    gcloud logging sinks update "$SINK_NAME" "$TOPIC_PATH" \
        --log-filter="$FILTER" \
        --project="$PROJECT_ID" || true
fi

WRITER_IDENTITY="$(gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" --format='value(writerIdentity)')"
echo "Granting roles/pubsub.publisher (least privilege) to log sink identity '${WRITER_IDENTITY}' on topic '${TOPIC}'..."
gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
    --member="$WRITER_IDENTITY" \
    --role="roles/pubsub.publisher" \
    --project="$PROJECT_ID" --quiet >/dev/null

# Step 4: Verify & Grant Platform Agent Service Account PubSub and Skill Command Permissions
echo "Step 4: Analyzing skill commands & checking Platform Agent GCP Service Account permissions..."

# Detect Platform Agent GCP Service Account from K8s SA annotation or default convention
GSA_EMAIL="$(kubectl --context="$CONTEXT" get sa kubeagents-platform-agent -n "$NAMESPACE" -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}' 2>/dev/null || echo "")"
if [ -z "$GSA_EMAIL" ]; then
    GSA_EMAIL="kubeagents-platform-gsa@${PROJECT_ID}.iam.gserviceaccount.com"
fi

echo "Platform Agent GCP Service Account identified: ${GSA_EMAIL}"

# 4a: Check & Grant PubSub topic & subscription permissions to Platform Agent GSA
echo "Checking PubSub topic/subscription access for Platform Agent GSA (${GSA_EMAIL})..."
gcloud pubsub topics add-iam-policy-binding "$TOPIC" \
    --member="serviceAccount:${GSA_EMAIL}" \
    --role="roles/pubsub.subscriber" \
    --project="$PROJECT_ID" --quiet >/dev/null

gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION" \
    --member="serviceAccount:${GSA_EMAIL}" \
    --role="roles/pubsub.subscriber" \
    --project="$PROJECT_ID" --quiet >/dev/null

# 4b: Skill Command Permission Analysis:
# Skill SKILL.md executes:
#   - `gcloud compute regions describe ...`
#   - `gcloud compute reservations list ...`
#   - `gcloud beta compute advice capacity ...`
#   - `gcloud beta compute advice capacity-history ...`
# Permission Required: `roles/compute.viewer` (Least privilege for GCP compute & capacity advice inspection)
echo "Skill Command Analysis: 'SKILL.md' executes gcloud compute & advice queries ('gcloud compute regions describe', 'gcloud beta compute advice ...')."
echo "Ensuring least-privilege IAM role 'roles/compute.viewer' is granted to '${GSA_EMAIL}'..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${GSA_EMAIL}" \
    --role="roles/compute.viewer" \
    --condition=None \
    --project="$PROJECT_ID" --quiet >/dev/null

# Step 5: Deploy AgentExtension via Helm
echo "Step 5: Deploying GKE Stockout Handler AgentExtension via Helm..."
helm upgrade --install gke-stockout-handler "$SCRIPT_DIR" \
    --kube-context "$CONTEXT" \
    --namespace "$NAMESPACE" \
    --create-namespace \
    --set clusterName="$CLUSTER_NAME" \
    --set pubsub.topic="$TOPIC" \
    --set pubsub.subscription="$SUBSCRIPTION"

echo "Step 6: Verifying AgentExtension status in cluster..."
kubectl --context="$CONTEXT" get agentextension gke-stockout-handler -n "$NAMESPACE"

echo "============================================================"
echo "GKE Stockout Handler Extension installation complete!"
echo "============================================================"
