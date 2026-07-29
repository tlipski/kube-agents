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
    
    print("=== LiteLLM Service ===")
    res = subprocess.run(["kubectl", "--context", context_name, "get", "svc", "litellm", "-n", NAMESPACE, "-o", "yaml"], capture_output=True, text=True, env=env)
    print(res.stdout)

    print("\n=== Test Connectivity from platform-agent pod to litellm ===")
    pod_res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = pod_res.stdout.strip()

    res = subprocess.run(["kubectl", "--context", context_name, "exec", "-n", NAMESPACE, pod_name, "-c", "platform-agent", "--", "curl", "-iv", "http://litellm:4000/health/liveliness"], capture_output=True, text=True, env=env)
    print("Port 4000 health:", res.stdout, res.stderr)

    res = subprocess.run(["kubectl", "--context", context_name, "exec", "-n", NAMESPACE, pod_name, "-c", "platform-agent", "--", "curl", "-iv", "http://litellm:80/health/liveliness"], capture_output=True, text=True, env=env)
    print("Port 80 health:", res.stdout, res.stderr)

if __name__ == "__main__":
    main()
