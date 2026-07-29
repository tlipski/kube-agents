#!/usr/bin/env python3
import subprocess
import os

UPSTREAM_DIR = "/tmp/ka-upstream"
LOCAL_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def read_file(path):
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""

def main():
    print("=== AgentExtension Types ===")
    print(read_file(os.path.join(UPSTREAM_DIR, "k8s-operator", "api", "v1alpha1", "agentextension_types.go")))

    print("\n=== Diff in platformagent_controller.go ===")
    res = subprocess.run([
        "diff", "-u",
        os.path.join(LOCAL_DIR, "k8s-operator", "internal", "controller", "platformagent_controller.go"),
        os.path.join(UPSTREAM_DIR, "k8s-operator", "internal", "controller", "platformagent_controller.go")
    ], capture_output=True, text=True)
    print(res.stdout[:5000])

    print("\n=== Diff in platformagent_manifests.go ===")
    res = subprocess.run([
        "diff", "-u",
        os.path.join(LOCAL_DIR, "k8s-operator", "internal", "controller", "platformagent_manifests.go"),
        os.path.join(UPSTREAM_DIR, "k8s-operator", "internal", "controller", "platformagent_manifests.go")
    ], capture_output=True, text=True)
    print(res.stdout[:10000])

if __name__ == "__main__":
    main()
