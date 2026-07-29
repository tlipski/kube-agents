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
    print("=== Operator Controller Manager Logs ===")
    res = subprocess.run(["kubectl", "--context", context_name, "logs", "-n", NAMESPACE, "-l", "control-plane=controller-manager", "--tail=100"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("=== Triggering PlatformAgent reconciliation by touching annotations ===")
    res = subprocess.run(["kubectl", "--context", context_name, "annotate", "platformagent", "platform-agent", "-n", NAMESPACE, f"reconcile-trigger={int(os.environ.get('EPOCH', 1))}", "--overwrite"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

if __name__ == "__main__":
    main()
