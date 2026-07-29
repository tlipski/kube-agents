#!/usr/bin/env python3
import subprocess
import os
import sys
import time

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west1"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
CONTEXT = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd, description=""):
    if description:
        print(f"\n=== {description} ===")
    print(f"Executing: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"ERROR ({res.returncode}): {res.stderr.strip()}")
        raise Exception(f"Command failed: {' '.join(cmd)}")
    return res.stdout.strip()

def main():
    print(f"=== Step 1: Connecting to {MGMT_CLUSTER} ({REGION}) ===")
    run_cmd(["gcloud", "container", "clusters", "get-credentials", MGMT_CLUSTER, "--region", REGION, "--project", PROJECT_ID])

    print(f"\n=== Step 2: Verifying Installed Operator Version ===")
    img = run_cmd(["kubectl", "--context", CONTEXT, "get", "deployment", "kubeagents-controller-manager", "-n", NAMESPACE, "-o", "jsonpath={.spec.template.spec.containers[0].image}"])
    print(f"Installed Operator Image: {img}")

    print(f"\n=== Step 3: Verifying Existing Installed Extensions ===")
    ext_list = run_cmd(["kubectl", "--context", CONTEXT, "get", "agentextension", "-n", NAMESPACE])
    print(f"Installed Extensions:\n{ext_list}")

    print(f"\n=== Step 4: Creating 2nd Test Extension with Invalid File Paths ===")
    test_extension_manifest = """apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentExtension
metadata:
  name: test-invalid-paths-extension
  namespace: kubeagents-system
spec:
  agentRef: platform-agent
  files:
    "platforms/../../../../../etc/password": "malicious_content_1"
    "../malicious/path.txt": "malicious_content_2"
    "skills/valid-test-skill/SKILL.md": "valid_content"
"""
    manifest_path = "/tmp/test_invalid_extension.yaml"
    with open(manifest_path, "w") as f:
        f.write(test_extension_manifest)

    run_cmd(["kubectl", "--context", CONTEXT, "apply", "-f", manifest_path])
    time.sleep(3)

    print(f"\n=== Step 5: Checking Operator Logs for Path Skip Warnings ===")
    logs = run_cmd(["kubectl", "--context", CONTEXT, "logs", "-n", NAMESPACE, "deployment/kubeagents-controller-manager", "-c", "manager", "--tail=100"])
    
    warning_found = False
    for line in logs.splitlines():
        if "Skipping invalid extension file path" in line:
            print(f"FOUND WARNING LOG: {line}")
            warning_found = True

    if not warning_found:
        print("WARNING LOG NOT FOUND IN RECENT LOGS! Full logs:")
        print(logs)
        sys.exit(1)
    else:
        print("SUCCESS: Operator logged skip warning for invalid file paths!")

    print(f"\n=== Step 6: Cleaning up Test Extension ===")
    run_cmd(["kubectl", "--context", CONTEXT, "delete", "-f", manifest_path, "--ignore-not-found=true"])
    print("Verification completed successfully!")

if __name__ == "__main__":
    main()
