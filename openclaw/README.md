# Kubernetes Agent - OpenClaw Integration

This directory contains the integration components for bringing specialized Kubernetes (k8s) AI agents and expert skills directly into the [OpenClaw](https://openclaw.ai/) ecosystem.

## What is Installed?

When you run the installation script, it enriches your OpenClaw environment with a suite of Kubernetes management capabilities:

1. **Specialized Subagents (e.g., `operator`)**
   - The installer creates dedicated, isolated AI subagents tailored for specific Kubernetes workflows.
   - The primary agent, **Kubernetes Operator** (`operator`), comes pre-configured with a custom identity (`IDENTITY.md`) and operational persona (`SOUL.md`), ensuring it acts as a knowledgeable, safety-conscious cluster operator.

2. **Domain-Specific Skills**
   - Agents are provisioned with targeted "Skills" (expert instructions and workflows) pre-bundled in their workspace.
   - For example, the `operator` agent comes equipped with skills like `gke-observability` out-of-the-box, providing it with structured playbooks for monitoring, metric analysis, and troubleshooting.
   *Note: While skill directories may retain cloud-specific configurations (e.g., GKE), the agent operates as a general Kubernetes Operator.*

3. **Semantic Routing Configuration**
   - The integration automatically patches OpenClaw's configuration to allow seamless semantic routing.
   - This means OpenClaw's main gateway can automatically detect Kubernetes-related queries and route them directly to the `operator` expert agent without manual intervention.

4. **Heartbeat Cronjobs**
   - Configures periodic health patrols and recommendation scans, allowing the agent to proactively monitor the cluster and report critical issues.

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

Once installation is complete, restart your OpenClaw gateway if it is already running. You can interact with your new Kubernetes expert immediately through your standard OpenClaw TUI or configured channels.

To start a session directly with the Kubernetes Operator agent, use:

```bash
openclaw tui --session agent:operator:main
```

## References

- [OpenClaw Documentation](https://docs.openclaw.ai/)
- [Building OpenClaw Plugins](https://docs.openclaw.ai/plugins/building-plugins)
