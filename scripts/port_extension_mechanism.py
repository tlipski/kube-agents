#!/usr/bin/env python3
import subprocess
import os
import shutil

UPSTREAM_DIR = "/tmp/ka-upstream/k8s-operator"
LOCAL_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev/k8s-operator"

files_to_port = [
    ("api/v1alpha1/agentextension_types.go", "api/v1alpha1/agentextension_types.go"),
    ("api/v1alpha1/common_types.go", "api/v1alpha1/common_types.go"),
    ("api/v1alpha1/zz_generated.deepcopy.go", "api/v1alpha1/zz_generated.deepcopy.go"),
    ("config/crd/bases/kubeagents.x-k8s.io_agentextensions.yaml", "config/crd/bases/kubeagents.x-k8s.io_agentextensions.yaml"),
    ("config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml", "config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml"),
    ("config/default/kustomization.yaml", "config/default/kustomization.yaml"),
    ("config/rbac/role.yaml", "config/rbac/role.yaml"),
    ("internal/controller/platformagent_controller.go", "internal/controller/platformagent_controller.go"),
    ("internal/controller/platformagent_manifests.go", "internal/controller/platformagent_manifests.go"),
    ("internal/controller/manifest_helpers.go", "internal/controller/manifest_helpers.go"),
    ("internal/controller/platformagent_manifests_test.go", "internal/controller/platformagent_manifests_test.go"),
    ("internal/webhook/platformagent_webhook.go", "internal/webhook/platformagent_webhook.go"),
    ("scripts/platform-agent.yaml.template", "scripts/platform-agent.yaml.template"),
]

def main():
    print("Porting AgentExtension files & configs from upstream feature/modular-agents...")
    for src_rel, dst_rel in files_to_port:
        src_path = os.path.join(UPSTREAM_DIR, src_rel)
        dst_path = os.path.join(LOCAL_DIR, dst_rel)
        if os.path.exists(src_path):
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(src_path, dst_path)
            print(f"Copied {src_rel} -> {dst_rel}")
        else:
            print(f"Warning: {src_rel} not found in upstream")

    # Copy config/agent_rbac directory
    agent_rbac_src = os.path.join(UPSTREAM_DIR, "config", "agent_rbac")
    agent_rbac_dst = os.path.join(LOCAL_DIR, "config", "agent_rbac")
    if os.path.exists(agent_rbac_src):
        shutil.copytree(agent_rbac_src, agent_rbac_dst, dirs_exist_ok=True)
        print("Copied config/agent_rbac directory")

    print("\nRunning `make install` to test CRD build...")
    res = subprocess.run(["make", "-C", LOCAL_DIR, "install"], capture_output=True, text=True)
    print("STDOUT:\n", res.stdout)
    print("STDERR:\n", res.stderr)

if __name__ == "__main__":
    main()
