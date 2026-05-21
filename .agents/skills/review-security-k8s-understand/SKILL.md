---
name: review-security-k8s-understand
description: Reads the details of the project and tries to build a reasonable understanding for context when doing security reviews.
---
# Task
Analyze the Kubernetes project/repository to build comprehensive architectural and security context for specialized review agents.

# Checks
- **Architecture**: Identify main components, workloads, and architecture.
- **Docs**: Read `README.md`, diagrams, or architecture docs.
- **Resources**: Identify K8s resource types deployed (Deployments, StatefulSets, CRDs).
- **Security Mechanisms**: Note existing security constraints/mechanisms.

# Output
Output a concise summary of project purpose, components, and security context.
