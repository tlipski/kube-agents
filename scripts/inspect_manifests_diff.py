#!/usr/bin/env python3
import subprocess
import os

UPSTREAM_DIR = "/tmp/ka-upstream"
LOCAL_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def main():
    print("=== Full Diff in platformagent_controller.go ===")
    res = subprocess.run([
        "diff", "-u",
        os.path.join(LOCAL_DIR, "k8s-operator", "internal", "controller", "platformagent_controller.go"),
        os.path.join(UPSTREAM_DIR, "k8s-operator", "internal", "controller", "platformagent_controller.go")
    ], capture_output=True, text=True)
    print(res.stdout)

    print("=== Full Diff in manifest_helpers.go ===")
    res = subprocess.run([
        "diff", "-u",
        os.path.join(LOCAL_DIR, "k8s-operator", "internal", "controller", "manifest_helpers.go"),
        os.path.join(UPSTREAM_DIR, "k8s-operator", "internal", "controller", "manifest_helpers.go")
    ], capture_output=True, text=True)
    print(res.stdout)

if __name__ == "__main__":
    main()
