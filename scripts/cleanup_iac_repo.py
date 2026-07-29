#!/usr/bin/env python3
import subprocess
import os
import shutil

PAT = os.environ.get("GITHUB_PAT")
if not PAT:
    print("Error: GITHUB_PAT environment variable not set")
    exit(1)

REPO_URL = f"https://x-access-token:{PAT}@github.com/tlipski/ka-dev-cluster1.git"
WORK_DIR = "/tmp/ka-dev-cluster1-iac-only"
SRC_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

if os.path.exists(WORK_DIR):
    shutil.rmtree(WORK_DIR)

os.makedirs(WORK_DIR)

# 1. Create clean IaC directory structure
os.makedirs(os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "namespaces"), exist_ok=True)
os.makedirs(os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "crds"), exist_ok=True)
os.makedirs(os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "agents"), exist_ok=True)
os.makedirs(os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "rbac"), exist_ok=True)

# 2. Copy Agent blueprints (declarative configuration, skills, personas)
shutil.copytree(os.path.join(SRC_DIR, "agents"), os.path.join(WORK_DIR, "agents"))

# 3. Copy CRDs to clusters/ka-dev-cluster1/crds/
crd_src = os.path.join(SRC_DIR, "k8s-operator", "config", "crd", "bases")
if os.path.exists(crd_src):
    shutil.copytree(crd_src, os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "crds"), dirs_exist_ok=True)

# 4. Create declarative cluster manifests in clusters/ka-dev-cluster1/
namespace_manifest = """apiVersion: v1
kind: Namespace
metadata:
  name: kubeagents-system
  labels:
    kubeagents.x-k8s.io/managed: "true"
"""
with open(os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "namespaces", "kubeagents-system.yaml"), "w") as f:
    f.write(namespace_manifest)

agent_cr_manifest = """apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: PlatformAgent
metadata:
  name: platform-agent
  namespace: kubeagents-system
spec:
  harness:
    clusterName: "ka-dev-cluster1"
    location: "europe-west3"
    hermes:
      dashboardEnabled: true
      pluginsDebug: false
      agentHome: "/opt/data"
      apiServerSecretRef:
        name: platform-agent-secrets
        key: API_SERVER_KEY
    memory:
      memoryEnabled: false
      provider: "multiuser_memory"
      userProfileEnabled: false
  deployment:
    image: "ghcr.io/gke-labs/kube-agents/platform-agent"
    tag: "latest"
    imagePullPolicy: IfNotPresent
  security:
    serviceAccountName: "kubeagents-platform-agent"
  integration:
    github:
      gitRepo: "tlipski/ka-dev-cluster1"
    googleChat:
      enabled: false
"""
with open(os.path.join(WORK_DIR, "clusters", "ka-dev-cluster1", "agents", "platform-agent.yaml"), "w") as f:
    f.write(agent_cr_manifest)

# 5. Create README.md for IaC repository
readme_content = """# ka-dev-cluster1 IaC Repository

This repository contains the Infrastructure-as-Code (IaC) definitions and declarative agent blueprints for the `ka-dev-cluster1` GKE cluster within the `kube-agents` harness framework.

## Structure

```
├── agents/                           # Declarative agent blueprints (personas, skills, prompts)
│   └── platform/                     # Platform Agent configuration
├── clusters/
│   └── ka-dev-cluster1/              # Declarative Kubernetes resources for ka-dev-cluster1
│       ├── agents/                   # PlatformAgent Custom Resources
│       ├── crds/                     # Custom Resource Definitions
│       ├── namespaces/               # Target namespace definitions
│       └── rbac/                     # Role and ServiceAccount definitions
└── README.md
```

All cluster resources and agent identities are managed declaratively.
"""
with open(os.path.join(WORK_DIR, "README.md"), "w") as f:
    f.write(readme_content)

# Git operations
def run_git(cmd, cwd=WORK_DIR):
    res = subprocess.run(["git"] + cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Git command {' '.join(cmd)} failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
    return res.returncode

run_git(["init"])
run_git(["config", "user.name", "Platform Agent Bot"])
run_git(["config", "user.email", "platform-agent@ka-dev.internal"])
run_git(["checkout", "-b", "main"])
run_git(["add", "."])
run_git(["commit", "-m", "chore: clean up ka-dev-cluster1 repo to contain only IaC code"])
run_git(["remote", "add", "origin", REPO_URL])

print("Pushing cleaned IaC repo to main branch...")
res = run_git(["push", "-u", "origin", "main", "--force"])

if res == 0:
    print("Successfully updated main branch in https://github.com/tlipski/ka-dev-cluster1 with IaC code only!")
else:
    print("Failed to push to GitHub repository.")
    exit(1)
