#!/usr/bin/env python3
import subprocess
import os
import time

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print("Scaling deployment platform-agent-gateway to 0...")
    subprocess.run(["kubectl", "--context", context_name, "scale", "deployment", "platform-agent-gateway", "-n", NAMESPACE, "--replicas=0"], check=True, env=env)
    time.sleep(2)
    print("Scaling deployment platform-agent-gateway back to 1...")
    subprocess.run(["kubectl", "--context", context_name, "scale", "deployment", "platform-agent-gateway", "-n", NAMESPACE, "--replicas=1"], check=True, env=env)
    print("Waiting for rollout status...")
    res = subprocess.run(["kubectl", "--context", context_name, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=180s"], capture_output=True, text=True, env=env)
    print(res.stdout)
    print(res.stderr)

if __name__ == "__main__":
    main()
