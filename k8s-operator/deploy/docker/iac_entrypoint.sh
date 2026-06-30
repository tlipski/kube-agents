#!/usr/bin/env bash
# ==============================================================================
# 🤖 IaC Deployer Entrypoint Script with State Persistence & Impersonation
# ==============================================================================
set -euo pipefail

echo "=== ENTRYPOINT DEBUG: Files in TF_DIR ==="
ls -la /app/k8s-operator/deploy/terraform/

ACTION="${1:-}"
if [ -z "${ACTION}" ]; then
  echo "Error: Action (provision|teardown) is required."
  echo "Usage: iac-entrypoint [provision|teardown] [args...]"
  exit 1
fi
shift

# Parse arguments to find cluster name and project ID
CLUSTER_NAME=""
PROJECT_ID=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[i]}" == "-c" || "${args[i]}" == "--cluster-name" ]]; then
    CLUSTER_NAME="${args[i+1]}"
  fi
  if [[ "${args[i]}" == "-p" || "${args[i]}" == "--project-id" ]]; then
    PROJECT_ID="${args[i+1]}"
  fi
done

if [ -z "${CLUSTER_NAME}" ]; then
  CLUSTER_NAME="kube-agents-dedicated-cluster"
fi
if [ -z "${PROJECT_ID}" ]; then
  PROJECT_ID="my-project-123"
fi

# Detect if we are running inside Kubernetes (GKE Job)
if [ -f "/var/run/secrets/kubernetes.io/serviceaccount/token" ]; then
  echo "Running inside Kubernetes. Workload Identity will handle GSA mapping."
  
  echo "=== ENTRYPOINT DEBUG: Active Identity ==="
  gcloud auth list
  
  echo "=== ENTRYPOINT DEBUG: Testing Token Acquisition ==="
  if gcloud auth print-access-token >/dev/null 2>&1; then
    echo "  ✓ Token acquisition successful."
  else
    echo "  ✗ Token acquisition failed."
    gcloud auth print-access-token || true
  fi

  # In-cluster: use the pod's service account token
  HOST_KUBECTL="env KUBECONFIG=/dev/null GOOGLE_IMPERSONATE_SERVICE_ACCOUNT= kubectl"
else
  echo "Running locally. Using active credentials."
  
  HOST_CLUSTER="${HOST_CLUSTER:-}"
  HOST_REGION="${HOST_REGION:-}"
  
  if [ -n "${HOST_CLUSTER}" ] && [ -n "${HOST_REGION}" ]; then
    echo "Configuring connection to host cluster ${HOST_CLUSTER} for state persistence..."
    export HOST_KUBECONFIG="/tmp/host-kubeconfig"
    env KUBECONFIG="${HOST_KUBECONFIG}" gcloud container clusters get-credentials "${HOST_CLUSTER}" --region "${HOST_REGION}" --project "${PROJECT_ID}" --quiet
    HOST_KUBECTL="env KUBECONFIG=${HOST_KUBECONFIG} GOOGLE_IMPERSONATE_SERVICE_ACCOUNT= kubectl"
  else
    echo "Warning: HOST_CLUSTER and HOST_REGION not set. State persistence will be disabled."
    # Fallback to dummy command to prevent failures
    HOST_KUBECTL="echo [State Disabled]; true"
  fi
fi

SECRET_NAME="tf-state-${CLUSTER_NAME}"
TF_STATE_PATH="/app/k8s-operator/deploy/terraform/terraform.tfstate.${CLUSTER_NAME}"

# 1. Restore state if it exists
if ${HOST_KUBECTL} get secret "${SECRET_NAME}" >/dev/null 2>&1; then
  echo "Found existing Terraform state in Kubernetes Secret ${SECRET_NAME}. Restoring..."
  ${HOST_KUBECTL} get secret "${SECRET_NAME}" -o jsonpath='{.data.state}' | base64 -d > "${TF_STATE_PATH}"
else
  echo "No existing Terraform state found in Kubernetes Secret ${SECRET_NAME}."
fi

# 2. Run the action
EXIT_CODE=0
if [ "${ACTION}" = "provision" ]; then
  echo "Starting provisioning..."
  /app/k8s-operator/scripts/provision_iac.sh "$@" || EXIT_CODE=$?
elif [ "${ACTION}" = "teardown" ]; then
  echo "Starting teardown..."
  /app/k8s-operator/scripts/teardown_iac.sh "$@" || EXIT_CODE=$?
else
  echo "Error: Invalid action '${ACTION}'. Must be 'provision' or 'teardown'."
  echo "Usage: iac-entrypoint [provision|teardown] [args...]"
  exit 1
fi

# 3. Persist or clean up state if the execution was successful
if [ ${EXIT_CODE} -eq 0 ]; then
  if [ "${ACTION}" = "provision" ]; then
    if [ -f "${TF_STATE_PATH}" ]; then
      echo "Saving Terraform state to Kubernetes Secret ${SECRET_NAME}..."
      ${HOST_KUBECTL} create secret generic "${SECRET_NAME}" \
        --from-file=state="${TF_STATE_PATH}" \
        --dry-run=client -o yaml | ${HOST_KUBECTL} apply -f -
    else
      echo "Warning: Provisioning succeeded but no state file was found at ${TF_STATE_PATH}."
    fi
  elif [ "${ACTION}" = "teardown" ]; then
    echo "Teardown succeeded. Deleting Terraform state Kubernetes Secret ${SECRET_NAME}..."
    ${HOST_KUBECTL} delete secret "${SECRET_NAME}" --ignore-not-found=true
  fi
else
  echo "Action '${ACTION}' failed with exit code ${EXIT_CODE}. State will not be updated."
fi

exit ${EXIT_CODE}
