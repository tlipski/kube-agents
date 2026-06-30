#!/usr/bin/env bash
# ==============================================================================
# 🛠️ Build and Push IaC Deployer Image (Local Docker or Cloud Build)
# ==============================================================================
set -euo pipefail

# ANSI Colors
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'

print_step() { echo -e "\n${C_BOLD}${C_CYAN}>>>${C_RESET} ${C_BOLD}$1${C_RESET} ${C_BOLD}${C_CYAN}<<<${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓${C_RESET} $1"; }
print_info() { echo -e "  ${C_CYAN}ℹ${C_RESET} $1"; }
print_error() { echo -e "  ${C_RED}✗${C_RESET} $1"; }

usage() {
  echo -e "${C_BOLD}Usage:${C_RESET} $0 [local|cloudbuild] [options]"
  echo ""
  echo -e "${C_BOLD}Build Options:${C_RESET}"
  echo "  -p, --project-id VALUE         GCP Project ID (default: active gcloud project)"
  echo "  -r, --registry-region VALUE    Artifact Registry region (default: us-central1)"
  echo ""
  echo -e "${C_BOLD}Deployment Options (accepted and ignored during build):${C_RESET}"
  echo "  -c, --cluster-name VALUE       Target GKE Cluster Name"
  echo "  -n, --namespace VALUE          Kubernetes namespace"
  echo "  -m, --model-provider VALUE     Model Provider"
  echo "  -d, --model-default-name VALUE Default Model Name"
  echo "  -u, --allowed-users VALUE      Allowed chat users"
  echo "  -go, --github-org VALUE        GitHub Organization"
  echo "  -gr, --github-repo VALUE       GitHub Repository"
  echo "  -ga, --github-app-id VALUE     GitHub App ID"
  echo "  -gp, --github-pem-path VALUE   GitHub Private Key path"
  echo ""
  exit 1
}

METHOD="${1:-}"
if [[ "${METHOD}" != "local" && "${METHOD}" != "cloudbuild" ]]; then
  print_error "First argument must be 'local' or 'cloudbuild'."
  usage
fi
shift

# Default variables
PROJECT_ID=""
REGISTRY_REGION="us-central1"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    -r|--registry-region)
      REGISTRY_REGION="$2"
      shift 2
      ;;
    -c|--cluster-name|-n|--namespace|-m|--model-provider|-d|--model-default-name|-u|--allowed-users|-go|--github-org|-gr|--github-repo|-ga|--github-app-id|-gp|--github-pem-path)
      # Accept and ignore deployment-only arguments to allow consistent parameter passing
      print_info "Option $1 is only used during deployment. Ignored for build."
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

IMAGE_TAG="${REGISTRY_REGION}-docker.pkg.dev/${PROJECT_ID}/kube-agents/iac-deployer:latest"
DOCKERFILE="deploy/docker/Dockerfile.iac"

if [ ! -f "${DOCKERFILE}" ]; then
  print_error "Dockerfile not found at ${DOCKERFILE}. Make sure you run this script from the repository root."
  exit 1
fi

if [[ "${METHOD}" == "local" ]]; then
  print_step "Building image locally using Docker: ${IMAGE_TAG}"
  if ! command -v docker &> /dev/null; then
    print_error "docker CLI is not installed. Cannot use 'local' method."
    exit 1
  fi
  
  docker build -f "${DOCKERFILE}" -t kube-agents-iac-deployer:latest .
  docker tag kube-agents-iac-deployer:latest "${IMAGE_TAG}"
  
  print_step "Pushing image to Artifact Registry..."
  docker push "${IMAGE_TAG}"
  print_success "Image successfully built and pushed locally."

elif [[ "${METHOD}" == "cloudbuild" ]]; then
  print_step "Building image in the cloud using Google Cloud Build: ${IMAGE_TAG}"
  if ! command -v gcloud &> /dev/null; then
    print_error "gcloud CLI is not installed. Cannot use 'cloudbuild' method."
    exit 1
  fi
  
  # Submit build to Cloud Build
  gcloud builds submit \
    --project="${PROJECT_ID}" \
    --config="deploy/docker/cloudbuild.iac.yaml" \
    --substitutions="_IMAGE_TAG=${IMAGE_TAG}" \
    .
    
  print_success "Image successfully built and pushed via Cloud Build."
fi
