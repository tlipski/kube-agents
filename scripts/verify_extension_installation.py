#!/usr/bin/env python3
import subprocess
import os

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west1"
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

    print("\n=== Init Container extension-installer Logs ===")
    res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "extension-installer", "-n", NAMESPACE], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Files in /opt/data/platforms/ inside platform-agent container ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "ls", "-la", "/opt/data/platforms"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Rendered config.yaml inside platform-agent container ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "cat", "/opt/data/config.yaml"], capture_output=True, text=True, env=env)
    print(res.stdout)

    print("\n=== Tailing container logs for hello world plugin ===")
    res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=50"], capture_output=True, text=True, env=env)
    print(res.stdout)
    print(res.stderr)

if __name__ == "__main__":
    main()
