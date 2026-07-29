# Pull Request

## Why This Change
We need a native Kubernetes way to dynamically inject configuration, secrets, environment variables, and files (such as skills or prompt templates) into `PlatformAgent` workloads without having to modify the base agent manifests or container image. 

## What Changed

**Files:**
- `k8s-operator/api/v1alpha1/agentplugin_types.go`: Defines the new `AgentPlugin` CRD schema (Spec and Status).
- `k8s-operator/api/v1alpha1/zz_generated.deepcopy.go`: Auto-generated deepcopy methods for the new CRD.
- `k8s-operator/config/crd/bases/kubeagents.x-k8s.io_agentplugins.yaml`: Auto-generated CRD manifest for `AgentPlugin`.
- `k8s-operator/config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml`: Reference the extensions.
- `k8s-operator/config/crd/kustomization.yaml`: Included the new `AgentPlugin` CRD in the Kustomize build.
- `k8s-operator/config/rbac/role.yaml`: Granted the operator RBAC permissions to read and watch `AgentPlugin` resources.
- `k8s-operator/internal/controller/platformagent_controller.go`: Updated the `PlatformAgent` reconciler to watch for and process `AgentPlugin` resources.
- `k8s-operator/internal/controller/platformagent_manifests.go`: Implemented the core logic to inject configs, files, and environment variables from `AgentPlugins` into the `PlatformAgent` pod specs.
- `k8s-operator/internal/controller/platformagent_manifests_test.go`: Added unit tests to verify the extension injection logic into the generated manifests.
- `k8s-operator/internal/controller/telemetry_test.go`: Updated test fixtures and assertions to accommodate the new manifest generation behavior.

**Added/Changed/Fixed:**
- **Added** the `AgentPlugin` CRD to enable on-the-fly composition of agent capabilities.
- **Added** file distribution capabilities (maps relative file paths directly to file contents within the agent's filesystem).
- **Added** configuration injection (merges raw YAML directly into the agent's core `config.yaml`).
- **Added** environment variable support for passing credentials securely (via standard Kubernetes `corev1.EnvVar` injection).
- **Added** selective targeting support via the optional `agentRef` field.

## Why This Matters
- It decouples agent-specific configurations and modular capabilities from the base agent manifests.
- It enables roll-outs of global behaviors or scoped specialized skills to particular agents effortlessly.
- It greatly simplifies integrations with external services by securely providing needed secrets as environment variables on demand.

## Context

This PR unlocks multiple new use cases for extending PlatformAgents dynamically. Here are sample CRDs demonstrating what is possible:

### Sample CRDs

#### 1. Injecting a New Skill and External Credentials
This extension adds a custom "Stockout Handler" skill to a specific agent and injects the required Slack API token securely from a pre-existing Kubernetes Secret.

```yaml
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentPlugin
metadata:
  name: gke-stockout-handler-skill
  namespace: kube-agents
spec:
  # Applies only to the "sre-agent-primary" PlatformAgent
  agentRef: "sre-agent-primary" 
  
  # Injects the SKILL.md file required for the capability
  files:
    "skills/gke-stockout-handler/SKILL.md": |
      name: GKE Stockout Handler
      description: Handles capacity stockout incidents on GKE
      instructions: |
        When you see a stockout event, identify the instance type and region, 
        and attempt to fall back to an alternative instance family or zone.
        
  # Mounts necessary secrets as environment variables
  env:
    - name: SLACK_API_TOKEN
      valueFrom:
        secretKeyRef:
          name: slack-secrets
          key: api-token
```

#### 2. Appending Global Configuration (e.g., Debug Logging)
This extension modifies the base configuration for **all** `PlatformAgent` instances in the namespace, which is useful for globally enabling debug logging without needing to restart or edit individual agent definitions.

```yaml
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentPlugin
metadata:
  name: global-debug-logging
  namespace: kube-agents
spec:
  # No agentRef provided - applies to all PlatformAgents in the namespace
  config: |
    logging:
      level: debug
      format: json
```

#### 3. Providing Custom System Prompts and Shell Scripts
This extension gives a specialized database agent its own custom prompt and a helper script for querying PostgreSQL, empowering the agent with domain-specific context and local tooling.

```yaml
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentPlugin
metadata:
  name: custom-db-admin-tools
  namespace: kube-agents
spec:
  agentRef: "database-agent"
  files:
    "prompts/system_prompt.txt": |
      You are an expert PostgreSQL Database Administrator. 
      Your main priority is query optimization, index management, and ensuring zero downtime.
      
    "scripts/check_pg_stat.sh": |
      #!/bin/bash
      # A helper script the agent can run to check active queries
      psql -c "SELECT pid, state, query FROM pg_stat_activity WHERE state != 'idle';"
```

## Testing

Tested extension installation, reconciliation logic, file generation, and environment injection into the generated pod spec.

---

Functional Impact: Safely introduces dynamic agent configuration without breaking existing behaviors or requiring immediate changes to current `PlatformAgent` definitions.
