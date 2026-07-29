#!/usr/bin/env python3
import subprocess
import os

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    
    # Get active pod name
    res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = res.stdout.strip()
    print("Pod name:", pod_name)

    print("\n=== Checking for [MOCK-EXTENSION-PLUGIN] in container logs ===")
    res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=200"], capture_output=True, text=True, env=env)
    
    found = False
    for line in res.stdout.splitlines() + res.stderr.splitlines():
        if "MOCK-EXTENSION-PLUGIN" in line or "hello world" in line:
            print("MATCH:", line)
            found = True

    if not found:
        print("\nFull STDOUT:\n", res.stdout)
        print("\nFull STDERR:\n", res.stderr)

if __name__ == "__main__":
    main()
