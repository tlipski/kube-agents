---
name: review-security-k8s-agents-prompt-injection
description: Reviews configurations and architectures for AI agents to identify prompt injection risks and inadequate sandboxing.
---
# Task
Review configurations, API gateways, and input architectures for prompt injection and malicious payload vulnerabilities.

# Checks
## 1. Input Sanitization & Proxies
- **Gateway/WAF**: Require LLM-specific API Gateway or WAF sidecar. Flag raw agent APIs exposed to untrusted traffic.
- **Guardrails**: Ensure system prompts/safety instructions in `ConfigMaps`/`EnvVars` cannot be tampered with by less privileged workloads.
