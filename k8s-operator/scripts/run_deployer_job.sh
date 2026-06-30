#!/usr/bin/env bash
# ==============================================================================
# 🤖 Run IaC Deployer as a GKE Job on autopilot-cluster-1
# ==============================================================================
set -euo pipefail

# ANSI Colors
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'

print_step() { echo -e "\n${C_BOLD}${C_CYAN}>>>${C_RESET} ${C_BOLD}$1${C_RESET} ${C_BOLD}${C_CYAN}<<<${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓${C_RESET} $1"; }
print_info() { echo -e "  ${C_CYAN}ℹ${C_RESET} $1"; }
print_error() { echo -e "  ${C_RED}✗${C_RESET} $1"; }

usage() {
  echo -e "${C_BOLD}Usage:${C_RESET} $0 [provision|teardown] [options]"
  echo ""
  echo -e "${C_BOLD}Required Options:${C_RESET}"
  echo "  -p, --project-id VALUE         Target GCP Project ID (default: active gcloud project)"
  echo "  -r, --region VALUE             GCP Region for the target cluster (default: us-east4)"
  echo "  -c, --cluster-name VALUE       Target GKE Cluster Name (default: kube-agents-dedicated-cluster)"
  echo "  -n, --namespace VALUE          Kubernetes namespace for deployment (default: kubeagents-system)"
  echo ""
  echo -e "${C_BOLD}Optional Options (passed to IaC scripts):${C_RESET}"
  echo "  -m, --model-provider VALUE     Model Provider: gemini, anthropic, openai, chatgpt (default: gemini)"
  echo "  -d, --model-default-name VALUE Default Model Name (default: gemini-3.5-flash)"
  echo "  -u, --allowed-users VALUE      Comma-separated list of allowed chat users (default: empty/all)"
  echo "  -go, --github-org VALUE        GitHub Organization/Owner name (for Token Minter)"
  echo "  -gr, --github-repo VALUE       GitHub Repository name (for Token Minter)"
  echo "  -ga, --github-app-id VALUE     GitHub App ID (for Token Minter)"
  echo "  -gp, --github-pem-path VALUE   GitHub App Private Key PEM file path (for KMS import)"
  echo ""
  exit 1
}

ACTION="${1:-}"
if [[ "${ACTION}" != "provision" && "${ACTION}" != "teardown" ]]; then
  print_error "First argument must be 'provision' or 'teardown'."
  usage
fi
shift

# Default variables
PROJECT_ID=""
REGION="us-east4"
CLUSTER_NAME="kube-agents-dedicated-cluster"
NAMESPACE="kubeagents-system"

# Parse remaining arguments and collect them for the GKE Job
JOB_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--project-id)
      PROJECT_ID="$2"
      JOB_ARGS+=("$1" "$2")
      shift 2
      ;;
    -r|--region)
      REGION="$2"
      JOB_ARGS+=("$1" "$2")
      shift 2
      ;;
    -c|--cluster-name)
      CLUSTER_NAME="$2"
      JOB_ARGS+=("$1" "$2")
      shift 2
      ;;
    -n|--namespace)
      NAMESPACE="$2"
      JOB_ARGS+=("$1" "$2")
      shift 2
      ;;
    *)
      # Pass through any other arguments (e.g. -m, -d, -u, -go, -gr, -ga, -gp)
      JOB_ARGS+=("$1")
      if [[ $# -gt 1 && "$2" != -* ]]; then
        JOB_ARGS+=("$2")
        shift 2
      else
        shift 1
      fi
      ;;
  esac
done

# Resolve project ID if not explicitly provided
if [ -z "${PROJECT_ID}" ]; then
  if command -v gcloud &>/dev/null; then
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
  fi
fi

if [ -z "${PROJECT_ID}" ]; then
  print_error "GCP Project ID is required. Please specify it using -p/--project-id or set your active project in gcloud."
  exit 1
fi

HOST_CLUSTER="autopilot-cluster-1"
HOST_REGION="us-central1"
GSA_NAME="ka-deployer-gsa"
GSA_EMAIL="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KSA_NAME="ka-deployer-ksa"
JOB_NAME="kube-agents-iac-deployer"

# Define explicit kubectl context for the host cluster to prevent race conditions
HOST_CONTEXT="gke_${PROJECT_ID}_${HOST_REGION}_${HOST_CLUSTER}"

# Step 1: Connect to the host cluster
print_step "Connecting to host GKE cluster ${HOST_CLUSTER} in ${HOST_REGION}"
gcloud container clusters get-credentials "${HOST_CLUSTER}" --region "${HOST_REGION}" --project "${PROJECT_ID}" --quiet

# Step 2: Create Google Service Account (GSA) if it doesn't exist
print_step "Ensuring Google Service Account ${GSA_EMAIL} exists"
if ! gcloud iam service-accounts describe "${GSA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${GSA_NAME}" \
    --display-name="Kube-agents IaC Deployer GSA" \
    --project="${PROJECT_ID}"
  print_success "GSA created."
else
  print_info "GSA already exists."
fi

# Step 3: Grant IAM Roles to GSA
print_step "Granting IAM roles to GSA"
ROLES=(
  "roles/editor"
  "roles/container.admin"
  "roles/resourcemanager.projectIamAdmin"
  "roles/pubsub.admin"
  "roles/cloudkms.admin"
  "roles/iam.securityAdmin"
)
for ROLE in "${ROLES[@]}"; do
  print_info "Granting ${ROLE}..."
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${GSA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet >/dev/null
done
print_success "IAM roles granted."

# Step 4: Create Kubernetes ServiceAccount (KSA)
print_step "Ensuring Kubernetes ServiceAccount ${KSA_NAME} exists in host cluster"
if ! kubectl --context="${HOST_CONTEXT}" get serviceaccount "${KSA_NAME}" >/dev/null 2>&1; then
  kubectl --context="${HOST_CONTEXT}" create serviceaccount "${KSA_NAME}"
  print_success "KSA created."
else
  print_info "KSA already exists."
fi

# Step 4.5: Grant RBAC permissions to KSA in host cluster (to allow saving state Secrets)
print_step "Granting RBAC admin permissions to KSA in host cluster"
if ! kubectl --context="${HOST_CONTEXT}" get rolebinding ka-deployer-admin-binding -n default >/dev/null 2>&1; then
  kubectl --context="${HOST_CONTEXT}" create rolebinding ka-deployer-admin-binding \
    --clusterrole=admin \
    --serviceaccount=default:${KSA_NAME} \
    --namespace=default
  print_success "RoleBinding created."
else
  print_info "RoleBinding already exists."
fi

# Step 5: Configure Workload Identity Binding
print_step "Configuring Workload Identity binding"
gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[default/${KSA_NAME}]" \
  --project="${PROJECT_ID}" \
  --quiet >/dev/null

kubectl --context="${HOST_CONTEXT}" annotate serviceaccount "${KSA_NAME}" \
  iam.gke.io/gcp-service-account="${GSA_EMAIL}" \
  --overwrite
print_success "Workload Identity configured."

# Step 6: Create Secret for GEMINI_API_KEY
print_step "Updating GEMINI_API_KEY secret in host cluster"
kubectl --context="${HOST_CONTEXT}" delete secret deployer-secrets --ignore-not-found=true
kubectl --context="${HOST_CONTEXT}" create secret generic deployer-secrets \
  --from-literal=GEMINI_API_KEY="${GEMINI_API_KEY:-placeholder}"
print_success "Secret updated."

# Step 6.5: Upload local Terraform state to Secret if it exists and secret doesn't exist
SECRET_NAME="tf-state-${CLUSTER_NAME}"
if ! kubectl --context="${HOST_CONTEXT}" get secret "${SECRET_NAME}" >/dev/null 2>&1; then
  STATE_FILE="k8s-operator/deploy/terraform/terraform.tfstate.${CLUSTER_NAME}"
  if [ -f "${STATE_FILE}" ]; then
    print_step "Uploading local Terraform state to host cluster Secret ${SECRET_NAME}"
    kubectl --context="${HOST_CONTEXT}" create secret generic "${SECRET_NAME}" \
      --from-file=state="${STATE_FILE}"
    print_success "State uploaded."
  fi
fi

# Step 7: Submit the Job
print_step "Submitting the IaC Deployer Job (${ACTION})"
kubectl --context="${HOST_CONTEXT}" delete job "${JOB_NAME}" --ignore-not-found=true

# Convert JOB_ARGS array to YAML list format for the Job manifest
JOB_ARGS_YAML=$(printf "        - \"%s\"\n" "${JOB_ARGS[@]}")

# Generate Job YAML on the fly
cat <<EOF | kubectl --context="${HOST_CONTEXT}" apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: default
spec:
  template:
    spec:
      serviceAccountName: ${KSA_NAME}
      containers:
      - name: deployer
        image: us-central1-docker.pkg.dev/${PROJECT_ID}/kube-agents/iac-deployer:latest
        imagePullPolicy: Always
        args:
        - "${ACTION}"
${JOB_ARGS_YAML}
        env:
        - name: GEMINI_API_KEY
          valueFrom:
            secretKeyRef:
              name: deployer-secrets
              key: GEMINI_API_KEY
              optional: true
      restartPolicy: Never
  backoffLimit: 0
EOF

# Step 8: Wait for Job pod to start and streaming logs
print_step "Waiting for Job pod to start and streaming logs..."
# Wait for the pod to be created
until kubectl --context="${HOST_CONTEXT}" get pods -l job-name="${JOB_NAME}" -o jsonpath='{.items[0].metadata.name}' >/dev/null 2>&1; do
  sleep 1
done

POD_NAME=$(kubectl --context="${HOST_CONTEXT}" get pods -l job-name="${JOB_NAME}" -o jsonpath='{.items[0].metadata.name}')
print_info "Pod name: ${POD_NAME}"

# Wait for the pod to be running or finished
kubectl --context="${HOST_CONTEXT}" wait --for=condition=Ready pod/${POD_NAME} --timeout=60s || true

# Stream logs
kubectl --context="${HOST_CONTEXT}" logs -f "${POD_NAME}"

# Check Job status
print_step "Checking Job execution result"
if kubectl --context="${HOST_CONTEXT}" wait --for=condition=complete "job/${JOB_NAME}" --timeout=30s >/dev/null 2>&1; then
  print_success "Job completed successfully!"
else
  print_error "Job failed or timed out. Please check the logs above."
  exit 1
fi
