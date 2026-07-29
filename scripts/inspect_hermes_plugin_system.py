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

    print("\n=== Content of /opt/hermes/hermes_cli/plugins.py ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "cat", "/opt/hermes/hermes_cli/plugins.py"], capture_output=True, text=True, env=env)
    print(res.stdout[:4000])

if __name__ == "__main__":
    main()
