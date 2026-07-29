#!/usr/bin/env python3
import subprocess
import os

REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def main():
    print("=== Git status ===")
    res = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    print(res.stdout)

    print("=== Checking diffs in k8s-operator ===")
    res = subprocess.run(["git", "diff", "k8s-operator/config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml"], cwd=REPO_DIR, capture_output=True, text=True)
    print("Diff in platformagents CRD:\n", res.stdout[:2000])

    res = subprocess.run(["git", "diff", "k8s-operator/config/webhook/manifests.yaml"], cwd=REPO_DIR, capture_output=True, text=True)
    print("Diff in webhook manifests.yaml:\n", res.stdout[:2000])

if __name__ == "__main__":
    main()
