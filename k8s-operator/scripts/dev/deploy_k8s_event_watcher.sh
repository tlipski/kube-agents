#!/usr/bin/env bash
# ==============================================================================
# 🛠️ Deploy Standalone k8s-event-watcher
# ==============================================================================
# Deploys or updates the k8s-event-watcher service on the host GKE cluster
# using the configured variables in vars.sh.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VARS_FILE="${SCRIPTS_DIR}/vars.sh"

if [ -f "$VARS_FILE" ]; then
  source "$VARS_FILE"
fi

PROJECT_ID="${PROJECT_ID:-dharb-gkedemos}"
REGION="${REGION:-us-east4}"
CLUSTER_NAME="${CLUSTER_NAME:-platform-agent-host}"
NAMESPACE="${NAMESPACE:-kubeagents-system}"
IMAGE_REPO="${GCP_ARTIFACT_REGISTRY_REPO_NAME:-kube-agents}"
IMAGE_TAG="${AGENT_TAG:-latest}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO}/platform-agent:${IMAGE_TAG}"

echo -e "🚀 Deploying k8s-event-watcher to GKE Cluster '${CLUSTER_NAME}' (${REGION})..."
echo -e "   Target Namespace: ${NAMESPACE}"
echo -e "   Image URI: ${IMAGE_URI}"

# 1. Verify cluster connection
echo -e "🔌 Connecting to GKE cluster..."
gcloud container clusters get-credentials "${CLUSTER_NAME}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1 || true

# 2. Ensure Namespace exists
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# 3. Apply standalone k8s-event-watcher Deployment & RBAC manifests
echo -e "📦 Applying k8s-event-watcher RBAC & Deployment manifests..."
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: k8s-event-watcher
  namespace: ${NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: k8s-event-watcher
rules:
- apiGroups: [""]
  resources: ["events", "pods", "nodes", "namespaces"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: k8s-event-watcher
subjects:
- kind: ServiceAccount
  name: k8s-event-watcher
  namespace: ${NAMESPACE}
roleRef:
  kind: ClusterRole
  name: k8s-event-watcher
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: k8s-event-watcher
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: k8s-event-watcher
    app.kubernetes.io/component: watcher
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: k8s-event-watcher
  template:
    metadata:
      labels:
        app.kubernetes.io/name: k8s-event-watcher
    spec:
      serviceAccountName: k8s-event-watcher
      containers:
      - name: event-watcher
        image: ${IMAGE_URI}
        imagePullPolicy: Always
        command:
        - /usr/local/bin/k8s-event-watcher
        args:
        - --cluster-name=${CLUSTER_NAME}
        - --daemon-url=http://platform-agent:8699
        - --token-env=API_SERVER_KEY
        - --owner=platform
        - --mode=per-incident
        - --reason=Failed,FailedToDrainNode,CrashLoopBackOff,BackOff,ImagePullBackOff,ErrImagePull,OOMKilled
        - --in-cluster
        env:
        - name: API_SERVER_KEY
          valueFrom:
            secretKeyRef:
              name: platform-agent-secrets
              key: API_SERVER_KEY
              optional: true
        resources:
          requests:
            cpu: "50m"
            memory: "64Mi"
          limits:
            cpu: "200m"
            memory: "256Mi"
EOF

# 4. Wait for rollout completion
echo -e "⏳ Waiting for k8s-event-watcher rollout to complete..."
kubectl rollout status deployment/k8s-event-watcher -n "${NAMESPACE}" --timeout=120s

echo -e "✅ k8s-event-watcher successfully deployed to namespace '${NAMESPACE}'!"
