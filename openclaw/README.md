# Kubernetes Agent - OpenClaw Integration

This directory contains the integration components for bringing specialized Kubernetes (k8s) AI agents and expert skills directly into the [OpenClaw](https://openclaw.ai/) ecosystem.

## What is Installed?

When you run the installation script, it enriches your OpenClaw environment with a multiagent cooperative layout and specialized Kubernetes skills:

1. **Kubernetes Operator Agent (`operator`)**: An autonomous custodian of the infrastructure. It manages global cluster concerns (multi-cluster balancing, capacity scaling, version upgrades, security patching) and executes scheduled operational cron tasks (health patrols, CVE scans, log rotations, backup validation).
2. **Development Team Agent (`devteam`)**: A production-safety coach and application workload custodian. It acts as the developers' first-responder, automating manifest validation, PR reviews (enforcing requests/limits and Pod Security Standards), canary rollouts, dependency management, and incident root-cause analysis.

---

## Agent Delegation & Routing Policy

The "main" agent acts as the primary orchestrator and dispatcher. It uses a strict routing guide (`ROUTING.md`) to safely delegate incoming developer requests to the most appropriate specialized subagent:

### 1. Quick Routing Commands (TUI & Shared Chat Shortcuts)
- **`@devteam <task>`**: Routes development-related work (writing code, manifests, build pipelines, rollouts, application-level bug fixes and debugging).
- **`@operator <task>`**: Routes cluster/platform operations (cluster health, scaling, upgrades, platform policies, cert scans, global security patches).
- **`@main <task>`**: Routes coordination, tradeoffs verification, planning, and human-in-the-loop communication.

### 2. Key Agent Responsibilities Matrix

| Feature Area | Primary Agent | Action Role |
|---|---|---|
| **App Code / Bug Fixes** | `devteam` | Complete code changes, compilation, staging debugging. |
| **Builds & Pipelines** | `devteam` | Manage Helm updates, container builds, SBOM verification. |
| **App Deployments** | `devteam` | Execute canary rollouts, monitor error thresholds (>1% auto-revert). |
| **Cluster Operations** | `operator` | Execute upgrades, tune fleet quotas, handle auto-remediation (e.g., restart hung kubelet). |
| **Platform Policies** | `operator` | Provision namespaces, enforce default-deny network policies. |
| **Coordination & Review** | `main` | Interpret user intent, verify subagent proof before reporting success. |

### 3. Strict Proof Gates
Before the main agent reports success to the human operator, it enforces strict proof validation gates:
- **For Development Tasks**: Requires Git commit SHAs, changed files listing, build/compilation terminal outputs, container digests (`@sha256`), and live deployment status evidence (`kubectl get deploy/pods/svc`).
- **For Operational Tasks**: Requires active context checking (`kubectl config current-context`), resource inspection scope, before/after state comparisons, and event/log evidence.

---

## Installation

You can install and configure the entire integration (agents, skills, and configuration) using a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/gke-labs/kube-agents/main/openclaw/scripts/install.sh | bash
```

### Installing from a Custom Branch or Fork

If you are testing from a fork or a custom branch, export the `REPO` and `BRANCH` environment variables first:

```bash
export REPO="https://github.com/<owner>/kube-agents"
export BRANCH="<branch-name>"
curl -fsSL "${REPO}/raw/${BRANCH}/openclaw/scripts/install.sh" | bash
```

---

## Getting Started

Once installation is complete, restart your OpenClaw gateway if it is already running.

You can interact with your new cooperative agent layout in two ways:

### 1. Chat with the Main Coordinator (Recommended)
To see the **Agent Delegation & Routing Policy** in action, start the standard OpenClaw TUI session (which connects you to the **Main Agent**):

```bash
openclaw tui
```

Once inside, you can use the routing shortcuts to delegate tasks:
- `@devteam Implement a new React checkout component in repo X...`
- `@operator Audit the current cluster egress policies...`
- Or simply describe your task and let `main` automatically interpret your intent and route it.

### 2. Chat Directly with a Subagent
If you want to open a direct session with a specialized agent (bypassing the coordinator), launch the TUI with their specific session key:

- **Kubernetes Operator**:
  ```bash
  openclaw tui --session agent:operator:main
  ```
- **Development Team Agent**:
  ```bash
  openclaw tui --session agent:devteam:main
  ```

## References

- [OpenClaw Documentation](https://docs.openclaw.ai/)
- [Building OpenClaw Plugins](https://docs.openclaw.ai/plugins/building-plugins)
