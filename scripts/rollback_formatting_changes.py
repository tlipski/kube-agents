#!/usr/bin/env python3
import subprocess
import os

REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

files_to_restore = [
    "k8s-operator/config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml",
    "k8s-operator/config/webhook/manifests.yaml",
    "k8s-operator/config/manager/kustomization.yaml"
]

def main():
    print("Reverting formatting-only changes...")
    for f in files_to_restore:
        res = subprocess.run(["git", "checkout", "HEAD", "--", f], cwd=REPO_DIR, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"Reverted formatting in: {f}")
        else:
            print(f"Error reverting {f}: {res.stderr}")

    print("\n=== Current git status in k8s-operator ===")
    res = subprocess.run(["git", "status", "k8s-operator"], cwd=REPO_DIR, capture_output=True, text=True)
    print(res.stdout)

if __name__ == "__main__":
    main()
