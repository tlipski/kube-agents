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
    
    print("=== kubectl get svc litellm ===")
    res = subprocess.run(["kubectl", "--context", context_name, "get", "svc", "litellm", "-n", NAMESPACE, "-o", "wide"], capture_output=True, text=True, env=env)
    print(res.stdout)

    print("=== kubectl get endpoints litellm ===")
    res = subprocess.run(["kubectl", "--context", context_name, "get", "endpoints", "litellm", "-n", NAMESPACE], capture_output=True, text=True, env=env)
    print(res.stdout)

if __name__ == "__main__":
    main()
