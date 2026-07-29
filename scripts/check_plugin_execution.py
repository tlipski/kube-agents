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

    print("\n=== Logs from Init Container extension-installer ===")
    res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "extension-installer", "-n", NAMESPACE], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Installed files in /opt/data/platforms ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "ls", "-la", "/opt/data/platforms"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Installed files in /opt/hermes/plugins/platforms ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "ls", "-la", "/opt/hermes/plugins/platforms"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Content of config.yaml inside pod ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "cat", "/opt/data/config.yaml"], capture_output=True, text=True, env=env)
    print(res.stdout)

    print("\n=== Full logs from platform-agent container ===")
    res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=100"], capture_output=True, text=True, env=env)
    print(res.stdout)
    print(res.stderr)

if __name__ == "__main__":
    main()
