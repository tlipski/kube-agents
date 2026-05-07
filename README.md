# kube-agents: The Kubernetes Agentic Harness

The k8s agentic harness will fundamentally redefine the DevOps presentation layer by replacing traditional interfaces like kubectl, gcloud, and the Pantheon console with intelligent, autonomous agents. By replacing the static, imperative nature of the traditional Kubernetes presentation layer with an autonomous agentic harness, we transition from reactive manual management to proactive, intent-driven operations.

## Key Components

### 1. Kubernetes Operator Agent (`operator`)
The core of the OpenClaw integration. A dedicated, safety-conscious AI agent configured with a calm, analytical persona (`SOUL.md`), designed to act as a senior Kubernetes Operator. It comes pre-configured to monitor cluster health, scan recommendations, and troubleshoot incidents.

The Operator agent is enriched with targeted skills (expert playbooks) for specialized tasks:
- **Observability**: Metrics, logging, and diagnostics.
- **Reliability**: High availability and workload stability.
- **Networking & Edge**: Ingress, gateways, and routing.
- **Security**: Workload hardening and vulnerability scanning.
- **Cost Analysis**: Analyzing GKE cost allocation and resource usage.
- ...and more.

---

## Installation & Setup

Choose how you want to deploy the Kubernetes agentic capabilities.

### Use in OpenClaw (Recommended)

You can install the specialized **Kubernetes Operator** agent and its bundled skills directly into your [OpenClaw](https://openclaw.ai/) workspace using a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/kube-agents/<branch>/openclaw/scripts/install.sh | bash
```
*(Note: Replace `<owner>` and `<branch>` with your repository details. Live GKE MCP server integration is an upcoming feature. In the meantime, the agent can still perform operations using standard shell commands).*

For more details, see the [OpenClaw Integration Guide](openclaw/README.md).

#### Installing from a Custom Branch or Fork

If you want to install from a forked repository or a custom branch (for example, to test changes), you should export `REPO` and `BRANCH` environment variables before running the install script. This ensures both `curl` and the installer use the correct sources:

```bash
export REPO="https://github.com/<owner>/kube-agents"
export BRANCH="<branch-name>"
curl -fsSL "${REPO}/raw/${BRANCH}/openclaw/scripts/install.sh" | bash
```

This will fetch the script from your branch and configure the installer to download assets from the same fork and branch.

## Disclaimer

This is not an officially supported Google product.

This project is not eligible for the Google Open Source Software Vulnerability Rewards Program.
