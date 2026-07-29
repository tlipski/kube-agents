#!/usr/bin/env python3
import subprocess
import os

REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

extension_related_files = [
    "k8s-operator/api/v1alpha1/agentextension_types.go",
    "k8s-operator/config/crd/bases/kubeagents.x-k8s.io_agentextensions.yaml",
    "k8s-operator/config/crd/kustomization.yaml"
]

non_extension_files = [
    "k8s-operator/api/v1alpha1/common_types.go",
    "k8s-operator/api/v1alpha1/zz_generated.deepcopy.go",
    "k8s-operator/config/agent_rbac/kustomization.yaml",
    "k8s-operator/config/agent_rbac/platformagent.yaml",
    "k8s-operator/config/default/kustomization.yaml",
    "k8s-operator/config/rbac/role.yaml",
    "k8s-operator/internal/controller/manifest_helpers.go",
    "k8s-operator/internal/controller/platformagent_controller.go",
    "k8s-operator/internal/controller/platformagent_manifests.go",
    "k8s-operator/internal/controller/platformagent_manifests_test.go",
    "k8s-operator/internal/webhook/platformagent_webhook.go",
    "k8s-operator/scripts/platform-agent.yaml.template"
]

def main():
    print("Unstaging extension-related files...")
    for f in extension_related_files:
        subprocess.run(["git", "restore", "--staged", f], cwd=REPO_DIR)

    print("\nStaging non-extension files...")
    for f in non_extension_files:
        subprocess.run(["git", "add", f], cwd=REPO_DIR)

    print("\n=== Current Git Staging Status ===")
    res = subprocess.run(["git", "status"], cwd=REPO_DIR, capture_output=True, text=True)
    print(res.stdout)

if __name__ == "__main__":
    main()
