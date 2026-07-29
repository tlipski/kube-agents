#!/usr/bin/env python3
import subprocess
import os

UPSTREAM_DIR = "/tmp/ka-upstream"
LOCAL_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def main():
    print("=== Git diff summary between local and upstream feature/modular-agents for k8s-operator ===")
    res = subprocess.run(
        ["diff", "-rq", "--exclude=.git", "--exclude=bin", os.path.join(LOCAL_DIR, "k8s-operator"), os.path.join(UPSTREAM_DIR, "k8s-operator")],
        capture_output=True, text=True
    )
    print(res.stdout)

    print("\n=== Listing API definitions in upstream k8s-operator/api ===")
    res = subprocess.run(["find", os.path.join(UPSTREAM_DIR, "k8s-operator", "api"), "-type", "f"], capture_output=True, text=True)
    print(res.stdout)

    print("\n=== Listing Controllers in upstream k8s-operator/internal/controller ===")
    res = subprocess.run(["find", os.path.join(UPSTREAM_DIR, "k8s-operator", "internal", "controller"), "-type", "f"], capture_output=True, text=True)
    print(res.stdout)

if __name__ == "__main__":
    main()
