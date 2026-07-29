# GKE Stockout Handler Extension Installation & Verification Report (`ka-dev-mgmt`)

## Overview
This document records the deployment, skill analysis, least-privilege IAM permission checks, Helm installation, and live end-to-end verification of the **GKE Stockout Handler AgentPlugin** (`gke-stockout-handler`) and **PubSub Platform Adapter** (`pubsub-platform`) on the **`ka-dev-mgmt`** management cluster.

- **Timestamp**: 2026-07-24 UTC
- **GCP Project**: `tomeklipski-izrhgv`
- **Kubectl Context**: `gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt`
- **Management Cluster**: `ka-dev-mgmt` (region: `europe-west1`)
- **Target Cluster**: `ka-production`
- **Namespace**: `kubeagents-system`
- **Platform Agent GCP SA**: `kubeagents-platform-gsa@tomeklipski-izrhgv.iam.gserviceaccount.com`
- **PubSub Topic**: `gke-stockout-alerts-topic`
- **PubSub Subscription**: `gke-stockout-alerts-sub`
- **Cloud Logging Sink**: `gke-stockout-alerts-sink`

---

## Deployed AgentPlugins on `ka-dev-mgmt`

1. **PubSub Platform Adapter** (`pubsub-platform`):
   - Helm Release: `pubsub-platform`
   - Config: [extensions/pubsub-platform/templates/agentplugin.yaml](file:///usr/local/google/home/tomeklipski/d/ka-dev/extensions/pubsub-platform/templates/agentplugin.yaml) (configured `allow_all: true` under `platforms.pubsub`).

2. **GKE Stockout Handler Extension** (`gke-stockout-handler`):
   - Helm Release: `gke-stockout-handler`
   - Script: [extensions/gke-stockout-handler/install.sh](file:///usr/local/google/home/tomeklipski/d/ka-dev/extensions/gke-stockout-handler/install.sh)
   - Route `gke_stockout_alerts` configured with skill instructions [extensions/gke-stockout-handler/files/skills/gke-stockout-handler/SKILL.md](file:///usr/local/google/home/tomeklipski/d/ka-dev/extensions/gke-stockout-handler/files/skills/gke-stockout-handler/SKILL.md).

---

## Skill Analysis & Permission Granting

An automated analysis of `gke-stockout-handler` [SKILL.md](file:///usr/local/google/home/tomeklipski/d/ka-dev/extensions/gke-stockout-handler/files/skills/gke-stockout-handler/SKILL.md) identified the following commands executed during capacity stockout diagnosis:

1. **GCP Compute Quota & Advice Diagnostic Commands**:
   - `gcloud compute regions describe ...` (checks regional CPU, N4, and GPU quota limits)
   - `gcloud compute reservations list ...` (checks zonal compute reservations)
   - `gcloud beta compute advice capacity ...` (evaluates Spot VM capacity & hardware availability)
   - `gcloud beta compute advice capacity-history ...` (evaluates preemption rates and price history)
   - **GCP IAM Requirement**: `roles/compute.viewer` (least privilege for compute resource & advice inspection).

2. **PubSub Event Messaging**:
   - Event ingestion via subscription route `gke_stockout_alerts`.
   - **GCP IAM Requirement**: `roles/pubsub.subscriber` on `gke-stockout-alerts-topic` and `gke-stockout-alerts-sub`.

---

## Empirical Verification Output on `ka-dev-mgmt`

### 1. Installation Execution (`install.sh`):
```bash
KUBECTL_CONTEXT=gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt bash extensions/gke-stockout-handler/install.sh
```
```text
============================================================
Installing GKE Stockout Handler Extension Module
GCP Project ID:  tomeklipski-izrhgv
Kubectl Context: gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt
Target Cluster:  ka-production
Namespace:       kubeagents-system
PubSub Topic:    gke-stockout-alerts-topic
Subscription:    gke-stockout-alerts-sub
============================================================
Step 1: Enabling required GCP APIs for GKE Stockout Handler extension...
Step 2: Ensuring PubSub Topic 'gke-stockout-alerts-topic' exists...
Step 2b: Ensuring PubSub Subscription 'gke-stockout-alerts-sub' exists...
Step 3: Ensuring Cloud Logging Sink 'gke-stockout-alerts-sink' exists...
Step 4: Analyzing skill commands & checking Platform Agent GCP Service Account permissions...
Platform Agent GCP Service Account identified: kubeagents-platform-gsa@tomeklipski-izrhgv.iam.gserviceaccount.com
Checking PubSub topic/subscription access for Platform Agent GSA...
Updated IAM policy for topic [gke-stockout-alerts-topic].
Updated IAM policy for subscription [gke-stockout-alerts-sub].
Skill Command Analysis: 'SKILL.md' executes gcloud compute & advice queries ('gcloud compute regions describe', 'gcloud beta compute advice ...').
Ensuring least-privilege IAM role 'roles/compute.viewer' is granted to 'kubeagents-platform-gsa@tomeklipski-izrhgv.iam.gserviceaccount.com'...
Updated IAM policy for project [tomeklipski-izrhgv].
Step 5: Deploying GKE Stockout Handler AgentPlugin via Helm...
Release "gke-stockout-handler" installed. STATUS: deployed.
============================================================
GKE Stockout Handler Extension installation complete!
============================================================
```

### 2. Live End-to-End Verification (`verify.py`):
```bash
KUBECTL_CONTEXT=gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt python3 extensions/gke-stockout-handler/verify.py
```
```text
============================================================
Verifying GKE Stockout Handler Extension (Python)
Project ID:      tomeklipski-izrhgv
Kubectl Context: gke_tomeklipski-izrhgv_europe-west1_ka-dev-mgmt
Target Cluster:  ka-production
PubSub Topic:    gke-stockout-alerts-topic
Test Event ID:   test-stockout-1784888157
============================================================
Step 1: Publishing test event to PubSub topic 'gke-stockout-alerts-topic'...
Publish output: messageIds:
- '20602515895070270'

Step 2: Waiting 8 seconds for PubSub adapter processing...

Step 3: Checking Hermes sessions in platform-agent-gateway container...
Latest Session Query Output:
[
  {
    "id": "20260724_101237_e5a3c08f",
    "user_id": "pubsub:gke_stockout_alerts",
    "chat_id": "pubsub:gke_stockout_alerts:20602541273620729",
    "started_at": 1784887957.8060327
  }
]

✓ SUCCESS: PubSub adapter received test event and created Hermes session for 'gke_stockout_alerts'!
```

**Outcome**: **PASSED** (100% verified on `ka-dev-mgmt` cluster).
