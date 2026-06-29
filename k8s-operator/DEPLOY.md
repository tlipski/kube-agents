# Manual Deployment Guide: kube-agents

This guide provides step-by-step instructions on how to manually deploy the GKE Platform Agent and Operator stack using **Terraform**, **Helm**, and **kubectl** directly, rather than using the automated `deploy_e2e_iac.sh` orchestrator.

---

## Prerequisites

Ensure you have the following CLI tools installed on your local workstation:
* [Google Cloud SDK (gcloud)](https://cloud.google.com/sdk/docs/install)
* [Terraform (>= 1.3.0)](https://developer.hashicorp.com/terraform/downloads)
* [Helm (>= 3.0.0)](https://helm.sh/docs/intro/install/)
* [kubectl](https://kubernetes.io/docs/tasks/tools/)
* [openssl](https://www.openssl.org/) (for generating API server keys)

---

## Step 1: Provision GCP Infrastructure with Terraform

The GCP infrastructure layer is managed by Terraform. It enables target GCP APIs, provisions the dedicated GKE Standard cluster, creates the four Google Service Accounts (GSAs), configures Workload Identity bindings, and sets up the Google Chat Pub/Sub topic and subscription.

1. Navigate to the Terraform directory:
   ```bash
   cd k8s-operator/deploy/terraform
   ```

2. Initialize the Terraform backend and provider plugins:
   ```bash
   terraform init
   ```

3. Provision the GCP resources. Make sure to specify your target Project ID, Region, Cluster Name, and Namespace. Optionally, specify your GitHub organization, repository, and App ID to provision resources for the GitHub Token Minter:
   ```bash
   terraform apply \
     -var="project_id=YOUR_PROJECT_ID" \
     -var="region=YOUR_GCP_REGION" \
     -var="cluster_name=YOUR_CLUSTER_NAME" \
     -var="namespace=YOUR_NAMESPACE" \
     -var="github_org=OPTIONAL_GITHUB_ORG" \
     -var="github_repo=OPTIONAL_GITHUB_REPO" \
     -var="github_app_id=OPTIONAL_GITHUB_APP_ID"
   ```
   *Example:*
   ```bash
   terraform apply \
     -var="project_id=my-project-123" \
     -var="region=us-east4" \
     -var="cluster_name=kube-agents-dedicated-cluster" \
     -var="namespace=kubeagents-system" \
     -var="github_org=my-github-org" \
     -var="github_repo=my-github-repo" \
     -var="github_app_id=123456"
   ```

4. Keep this terminal open or note down the **Outputs** displayed at the end of the `terraform apply` run. You will need them in Step 4.

---

## Step 2: Configure kubectl Credentials

Connect your local `kubectl` client to the newly created GKE cluster:

```bash
gcloud container clusters get-credentials YOUR_CLUSTER_NAME \
  --region YOUR_GCP_REGION \
  --project YOUR_PROJECT_ID
```
*Example:*
```bash
gcloud container clusters get-credentials kube-agents-dedicated-cluster \
  --region us-east4 \
  --project my-project-123
```

Verify you can connect to the cluster and list the nodes:
```bash
kubectl get nodes
```

## Step 3: Generate API Key & Deploy the Workloads via Helm

Now, we deploy the Helm chart. The Helm chart will **automatically create and manage every single Kubernetes resource**, including the namespace (`kubeagents-system`) and the API secrets (`platform-agent-secrets`). There is no need to run manual `kubectl create` commands.

We will generate a secure API Server Key, read the GCP Service Account emails from the **Terraform Outputs** (Step 1), and pass all credentials directly to Helm.

1. Generate a secure, 16-byte random hex key locally:
   ```bash
   API_SERVER_KEY=$(openssl rand -hex 16)
   echo "Your API Server Key is: ${API_SERVER_KEY}"
   ```

2. If you need to view the Terraform outputs again, run `terraform output` from the `deploy/terraform` directory:
   ```bash
   cd k8s-operator/deploy/terraform
   terraform output
   ```

3. If you enabled the GitHub Token Minter, you must import your GitHub App private key PEM into Google Cloud KMS before deploying the workloads. Run the following command:
   ```bash
   git clone --depth 1 --branch v2.7.1 https://github.com/abcxyz/github-token-minter.git /tmp/minty
   cd /tmp/minty
   go run ./cmd/minty tools import-pk \
     -project-id="YOUR_PROJECT_ID" \
     -location="YOUR_GCP_REGION" \
     -key-ring="OUTPUT_KMS_KEYRING" \
     -key="OUTPUT_KMS_KEY" \
     -private-key="@/path/to/your/github-app-private-key.pem"
   ```
   *Note:* After a successful import, resolve the active version number:
   ```bash
   gcloud kms keys versions list --key="OUTPUT_KMS_KEY" --keyring="OUTPUT_KMS_KEYRING" --location="YOUR_GCP_REGION" --project="YOUR_PROJECT_ID" --filter="state=ENABLED" --format="value(name)" | awk -F'/' '{print $NF}' | sort -n | tail -n 1
   ```
   This version number (usually `1` on first import) must be passed to Helm as `KMS_KEY_VERSION`.

4. Run the `helm upgrade --install` command to deploy the entire stack. Replace the GSA email outputs, project details, and your real API keys. If the GitHub Token Minter is enabled, pass its configuration parameters:
   ```bash
   helm upgrade --install kube-agents ../helm/kube-agents \
     --namespace "YOUR_NAMESPACE" \
     --create-namespace \
     --set projectId="YOUR_PROJECT_ID" \
     --set clusterName="YOUR_CLUSTER_NAME" \
     --set clusterLocation="YOUR_GCP_REGION" \
     --set operator.controllerGsaEmail="OUTPUT_CONTROLLER_GSA_EMAIL" \
     --set agents.platform.gsaName="kubeagents-platform-gsa" \
     --set agents.platform.gsaEmail="OUTPUT_PLATFORM_AGENT_GSA_EMAIL" \
     --set agents.operator.gsaEmail="OUTPUT_OPERATOR_AGENT_GSA_EMAIL" \
     --set agents.devteam.gsaEmail="OUTPUT_DEVTEAM_AGENT_GSA_EMAIL" \
     --set model.provider="gemini" \
     --set model.defaultName="gemini-3.5-flash" \
     --set keys.geminiApiKey="YOUR_GEMINI_API_KEY" \
     --set keys.apiServerKey="${API_SERVER_KEY}" \
     --set gchat.topicName="platform-agent-chat-events" \
     --set gchat.subscriptionName="platform-agent-chat-events-sub" \
     --set githubMinter.enabled=true \
     --set githubMinter.gsaEmail="OUTPUT_GITHUB_MINTER_GSA_EMAIL" \
     --set githubMinter.kmsKeyring="OUTPUT_KMS_KEYRING" \
     --set githubMinter.kmsKey="OUTPUT_KMS_KEY" \
     --set githubMinter.kmsKeyVersion="KMS_KEY_VERSION" \
     --set githubMinter.githubOrg="YOUR_GITHUB_ORG" \
     --set githubMinter.githubRepo="YOUR_GITHUB_REPO" \
     --set githubMinter.githubAppId="YOUR_GITHUB_APP_ID"
   ```

---

## Step 5: Verify the Rollout

Verify that all pods are successfully created and running:

```bash
kubectl get pods -n YOUR_NAMESPACE
```

You should see the following pods in the `Running` state:
* `kubeagents-controller-manager-...` (The operator)
* `litellm-...` (LiteLLM gateway - 2 replicas)
* `platform-agent-gateway-...` (The platform agent bot gateway - 2/2 containers running)
