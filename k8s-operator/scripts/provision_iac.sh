#!/usr/bin/env bash
# ==============================================================================
# 🤖 Hybrid E2E Terraform & Local Helm Deployment and Verification Script
# ==============================================================================
set -euo pipefail

# Google Environment Terraform Wrapper Function with state isolation
terraform() {
  local cmd="$1"
  shift
  if [[ "$cmd" == "apply" || "$cmd" == "output" || "$cmd" == "destroy" || "$cmd" == "plan" ]]; then
    /google/bin/releases/g3terraform/runner_main --base_service_dir="$(pwd)" --tf_label='terraform_1_13_5' "$cmd" -state="terraform.tfstate.${CLUSTER_NAME}" "$@"
  else
    /google/bin/releases/g3terraform/runner_main --base_service_dir="$(pwd)" --tf_label='terraform_1_13_5' "$cmd" "$@"
  fi
}

# Clear HTTP/HTTPS proxy variables for local commands to ensure direct connections
# to GKE control plane endpoints bypass any corporate proxies.
export HTTP_PROXY=""
export HTTPS_PROXY=""
export http_proxy=""
export https_proxy=""
export NO_PROXY="*"

# ANSI Colors
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_RED='\033[91m'
C_WHITE='\033[97m'
C_RESET='\033[0m'
C_BOLD='\033[1m'

print_step() { echo -e "\n${C_BOLD}${C_CYAN}>>>  $1  <<<${C_RESET}"; }
print_success() { echo -e "${C_GREEN}✓ $1${C_RESET}"; }
print_info() { echo -e "${C_CYAN}ℹ $1${C_RESET}"; }
print_error() { echo -e "${C_RED}✗ $1${C_RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TF_DIR="${ROOT_DIR}/deploy/terraform"

# Usage / Help Message
usage() {
  echo -e "${C_BOLD}Usage:${C_RESET} $0 [required-options] [optional-options]"
  echo ""
  echo -e "${C_BOLD}Required Options:${C_RESET}"
  echo "  -p, --project-id VALUE         Target GCP Project ID"
  echo "  -r, --region VALUE             GCP Region for the GKE cluster"
  echo "  -c, --cluster-name VALUE       GKE Cluster Name"
  echo "  -n, --namespace VALUE          Kubernetes namespace for deployment"
  echo ""
  echo -e "${C_BOLD}Optional Options:${C_RESET}"
  echo "  -m, --model-provider VALUE     Model Provider: gemini, anthropic, openai, chatgpt (default: gemini)"
  echo "  -d, --model-default-name VALUE Default Model Name (default: gemini-3.5-flash)"
  echo "  -u, --allowed-users VALUE      Comma-separated list of allowed chat users (default: empty/all)"
  echo "  -h, --help                     Display this help message and exit"
  echo ""
  exit 1
}

# Initialize variables
PROJECT_ID=""
REGION=""
CLUSTER_NAME=""
NAMESPACE=""
MODEL_PROVIDER="gemini"
MODEL_DEFAULT_NAME="gemini-3.5-flash"
ALLOWED_USERS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    -r|--region)
      REGION="$2"
      shift 2
      ;;
    -c|--cluster-name)
      CLUSTER_NAME="$2"
      shift 2
      ;;
    -n|--namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    -m|--model-provider)
      MODEL_PROVIDER="$2"
      shift 2
      ;;
    -d|--model-default-name)
      MODEL_DEFAULT_NAME="$2"
      shift 2
      ;;
    -u|--allowed-users)
      ALLOWED_USERS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      print_error "Unknown option: $1"
      usage
      ;;
  esac
done

# Validate the first four (required) parameters
if [ -z "${PROJECT_ID}" ] || [ -z "${REGION}" ] || [ -z "${CLUSTER_NAME}" ] || [ -z "${NAMESPACE}" ]; then
  print_error "Missing required arguments: project-id, region, cluster-name, and namespace must all be specified."
  echo ""
  usage
fi

# Detect if GEMINI_API_KEY is available in the local environment
TF_VARS=""
if [ -n "${GEMINI_API_KEY:-}" ]; then
  print_info "GEMINI_API_KEY detected in environment. Will pass it to Helm."
else
  print_info "No GEMINI_API_KEY found in environment. Using default placeholder."
fi

# Generate unique GSA and Pub/Sub names for non-default clusters
if [ "${CLUSTER_NAME}" != "kube-agents-dedicated-cluster" ]; then
  # Remove "kube-agents-" prefix if present to make suffix shorter and readable
  SUFFIX="${CLUSTER_NAME#kube-agents-}"
  # Clean up suffix to only allow valid GSA characters
  SUFFIX=$(echo "${SUFFIX}" | tr -cd 'a-zA-Z0-9-')
  CONTROLLER_GSA=$(echo "ka-ctrl-${SUFFIX}" | cut -c1-30)
  PLATFORM_GSA=$(echo "ka-plat-${SUFFIX}" | cut -c1-30)
  OPERATOR_GSA=$(echo "ka-oper-${SUFFIX}" | cut -c1-30)
  DEVTEAM_GSA=$(echo "ka-dev-${SUFFIX}" | cut -c1-30)
  TOPIC_NAME="platform-agent-chat-events-${SUFFIX}"
  SUB_NAME="platform-agent-chat-events-sub-${SUFFIX}"
  DELETION_PROTECTION="false"
else
  CONTROLLER_GSA="kubeagents-controller-gsa"
  PLATFORM_GSA="kubeagents-platform-gsa"
  OPERATOR_GSA="kubeagents-operator-gsa"
  DEVTEAM_GSA="kubeagents-devteam-gsa"
  TOPIC_NAME="platform-agent-chat-events"
  SUB_NAME="platform-agent-chat-events-sub"
  DELETION_PROTECTION="true"
fi

# Step 1: Initialize Terraform
print_step "Initializing Terraform"
cd "${TF_DIR}"
terraform init

# Step 2: Apply Terraform Configuration (Only GCP Infrastructure)
print_step "Applying Terraform Configuration (GCP Resources & GKE Cluster)"
terraform apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="cluster_name=${CLUSTER_NAME}" \
  -var="namespace=${NAMESPACE}" \
  -var="controller_gsa_name=${CONTROLLER_GSA}" \
  -var="platform_gsa_name=${PLATFORM_GSA}" \
  -var="operator_gsa_name=${OPERATOR_GSA}" \
  -var="devteam_gsa_name=${DEVTEAM_GSA}" \
  -var="gchat_topic_name=${TOPIC_NAME}" \
  -var="gchat_subscription_name=${SUB_NAME}" \
  -var="deletion_protection=${DELETION_PROTECTION}"

# Step 3: Read Terraform Outputs
print_step "Reading Terraform Outputs for Workload Configuration"
CONTROLLER_GSA_EMAIL=$(terraform output -raw controller_gsa_email)
PLATFORM_GSA_EMAIL=$(terraform output -raw platform_agent_gsa_email)
OPERATOR_GSA_EMAIL=$(terraform output -raw operator_agent_gsa_email)
DEVTEAM_GSA_EMAIL=$(terraform output -raw devteam_agent_gsa_email)
TOPIC_ID=$(terraform output -raw gchat_pubsub_topic)
SUB_ID=$(terraform output -raw gchat_pubsub_subscription)

# Extract short resource names from the full GCP resource paths
TOPIC_NAME=$(basename "${TOPIC_ID}")
SUB_NAME=$(basename "${SUB_ID}")

print_info "Controller GSA: ${CONTROLLER_GSA_EMAIL}"
print_info "Platform GSA:   ${PLATFORM_GSA_EMAIL}"
print_info "Pub/Sub Topic:  ${TOPIC_NAME}"

# Step 4: Generate Secure API Server Key locally
print_step "Generating Secure API Server Key"
API_SERVER_KEY=$(openssl rand -hex 16)
print_success "API Server Key generated successfully."

# Step 5: Fetch GKE Credentials
print_step "Connecting kubectl to the GKE cluster"
gcloud container clusters get-credentials "${CLUSTER_NAME}" --region "${REGION}" --project "${PROJECT_ID}" --quiet
KUBE_CONTEXT="gke_${PROJECT_ID}_${REGION}_${CLUSTER_NAME}"

# Step 5.5: Apply Custom Resource Definitions (CRDs)
print_step "Applying Custom Resource Definitions (CRDs) from config/crd/bases"
kubectl --context="${KUBE_CONTEXT}" apply -f "${ROOT_DIR}/config/crd/bases/"

# Step 6: Deploy Workloads via local Helm CLI
print_step "Deploying Workloads via Local Helm CLI"
HELM_CHART_PATH="${ROOT_DIR}/deploy/helm/kube-agents"

helm --kube-context="${KUBE_CONTEXT}" upgrade --install kube-agents "${HELM_CHART_PATH}" \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --set global.namespace="${NAMESPACE}" \
  --set projectId="${PROJECT_ID}" \
  --set clusterName="${CLUSTER_NAME}" \
  --set clusterLocation="${REGION}" \
  --set operator.controllerGsaEmail="${CONTROLLER_GSA_EMAIL}" \
  --set agents.platform.gsaName="${PLATFORM_GSA}" \
  --set agents.platform.gsaEmail="${PLATFORM_GSA_EMAIL}" \
  --set agents.operator.gsaEmail="${OPERATOR_GSA_EMAIL}" \
  --set agents.devteam.gsaEmail="${DEVTEAM_GSA_EMAIL}" \
  --set model.provider="${MODEL_PROVIDER}" \
  --set model.defaultName="${MODEL_DEFAULT_NAME}" \
  --set keys.geminiApiKey="${GEMINI_API_KEY:-placeholder}" \
  --set keys.apiServerKey="${API_SERVER_KEY}" \
  --set gchat.topicName="${TOPIC_NAME}" \
  --set gchat.subscriptionName="${SUB_NAME}" \
  --set gchat.allowedUsers="${ALLOWED_USERS}"

# Step 6.5: Force rollout restart of deployments to refresh secrets in running pods
print_step "Forcing rollout restart of deployments to refresh secrets"
kubectl --context="${KUBE_CONTEXT}" rollout restart deployment/litellm -n "${NAMESPACE}"
if kubectl --context="${KUBE_CONTEXT}" get deployment/platform-agent-gateway -n "${NAMESPACE}" >/dev/null 2>&1; then
  kubectl --context="${KUBE_CONTEXT}" rollout restart deployment/platform-agent-gateway -n "${NAMESPACE}"
fi

# Step 7: Verify namespace
print_step "Verifying Namespace ${NAMESPACE}"
until kubectl --context="${KUBE_CONTEXT}" get namespace "${NAMESPACE}" >/dev/null 2>&1; do
  print_info "Waiting for namespace ${NAMESPACE} to be active..."
  sleep 5
done
print_success "Namespace ${NAMESPACE} is active."

# Step 8: Wait for Operator rollout
print_step "Waiting for Operator Deployment to roll out"
kubectl --context="${KUBE_CONTEXT}" rollout status deployment/kubeagents-controller-manager -n "${NAMESPACE}" --timeout=120s
print_success "Operator is running."

# Step 9: Wait for LiteLLM rollout
print_step "Waiting for LiteLLM Gateway Deployment to roll out"
kubectl --context="${KUBE_CONTEXT}" rollout status deployment/litellm -n "${NAMESPACE}" --timeout=120s
print_success "LiteLLM Gateway is running."

# Step 10: Wait for PlatformAgent Gateway rollout (created by the operator)
print_step "Waiting for Platform Agent Gateway to roll out (managed by the operator)"
until kubectl --context="${KUBE_CONTEXT}" get deployment/platform-agent-gateway -n "${NAMESPACE}" >/dev/null 2>&1; do
  print_info "Waiting for platform-agent-gateway deployment to be created by the operator..."
  sleep 5
done

kubectl --context="${KUBE_CONTEXT}" rollout status deployment/platform-agent-gateway -n "${NAMESPACE}" --timeout=180s
print_success "Platform Agent Gateway is running."

# Step 11: Final Verification of Pods
print_step "Final Status of Pods in Namespace ${NAMESPACE}"
kubectl --context="${KUBE_CONTEXT}" get pods -n "${NAMESPACE}"

print_success "End-to-End Hybrid Deployment completed and verified successfully!"
echo -e "\n${C_BOLD}${C_GREEN}Your kube-agents operator, LiteLLM gateway, and Platform Agent bot are fully deployed and running in a dedicated new GKE cluster!${C_RESET}"

echo -e "\n${C_YELLOW}${C_BOLD}======================= START COPY&PASTE =======================${C_RESET}"
echo -e "${C_YELLOW}Your Kubernetes Operator and Custom Resources are ready!${C_RESET}"
echo -e "Next steps to configure and interact with your bot:\n"

echo -e "[ ] 1. Configure GChat bot connection in GCP Console:"
echo -e "       ${C_WHITE}https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat?project=${PROJECT_ID}${C_RESET}"
echo -e "       - Name: ${C_GREEN}GKE Platform Agent Bot${C_RESET}"
echo -e "       - Avatar: ${C_GREEN}https://platform-agent.nousresearch.com/docs/img/logo.png${C_RESET}"
echo -e "       - Connection Settings: Select ${C_BOLD}Cloud Pub/Sub${C_RESET}"
echo -e "       - Pub/Sub Topic Name: ${C_GREEN}projects/${PROJECT_ID}/topics/${TOPIC_NAME}${C_RESET}"
echo -e "       - Under Visibility, check: ${C_GREEN}Only specific people (add your email/emails: ${ALLOWED_USERS:-your-email})${C_RESET}"

echo -e ""
echo -e "[ ] 2. Send a DM to the Bot on Google Chat:"
echo -e "       Type: ${C_WHITE}\"Hi Hermes\"${C_RESET}"

echo -e ""
echo -e "[ ] 3. ${C_YELLOW}[Optional]${C_RESET} Approve pairing code in GKE container:"
echo -e "       ${C_CYAN}(Only required for first-time bot deployments. If the bot responds instantly, skip this!)${C_RESET}"
echo -e "       ${C_WHITE}kubectl exec -it deploy/platform-agent-gateway -n ${NAMESPACE} -- hermes pairing approve google_chat <PAIRING_CODE>${C_RESET}"
if [ "$MODEL_PROVIDER" = "chatgpt" ]; then
  echo -e ""
  echo -e "[ ] 4. ${C_YELLOW}Complete ChatGPT OAuth Device Flow Authentication:${C_RESET}"
  echo -e "       Because you selected 'chatgpt' as the model provider, LiteLLM must be authenticated"
  echo -e "       via OpenAI's OAuth Device Flow. Please follow these steps to authenticate:"
  echo -e "       - View the LiteLLM gateway logs to retrieve the 8-digit user code:"
  echo -e "         ${C_WHITE}kubectl logs -n ${NAMESPACE} deployment/litellm -f${C_RESET}"
  echo -e "       - Open your browser and navigate to: ${C_WHITE}https://auth.openai.com/codex/device${C_RESET}"
  echo -e "       - Enter the code displayed in the LiteLLM logs and log in to authorize the device."
  echo -e "       - Once authorized, the LiteLLM gateway will automatically pair with your ChatGPT subscription."
fi

echo -e "======================== END COPY&PASTE ========================\n"
