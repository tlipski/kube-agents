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
    print("=== Step 1: Triggering PlatformAgent reconciliation ===")
    subprocess.run(["kubectl", "--context", context_name, "annotate", "platformagent", "platform-agent", "-n", NAMESPACE, f"reconcile-ts={int(time.time())}", "--overwrite"], check=True, env=env)

    print("=== Step 2: Waiting 5s for Operator Reconciliation ===")
    time.sleep(5)

    print("=== Step 3: Checking platform-agent-extensions ConfigMap ===")
    res = subprocess.run(["kubectl", "--context", context_name, "get", "cm", "platform-agent-extensions", "-n", NAMESPACE, "-o", "yaml"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("=== Step 4: Waiting for platform-agent-gateway rollout status ===")
    subprocess.run(["kubectl", "--context", context_name, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=180s"], env=env)

    print("=== Step 5: Tailing platform-agent container logs for plugin registration ===")
    time.sleep(10)
    pod_res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = pod_res.stdout.strip()
    print("Pod name:", pod_name)

    log_res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=100"], capture_output=True, text=True, env=env)
    print(log_res.stdout)
    print(log_res.stderr)

if __name__ == "__main__":
    main()
