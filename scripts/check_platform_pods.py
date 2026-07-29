#!/usr/bin/env python3
import subprocess
import os

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return res.stdout.strip(), res.stderr.strip(), res.returncode

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    
    print("=== PlatformAgent Status ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "get", "platformagent", "platform-agent", "-n", NAMESPACE, "-o", "yaml"])
    print(out if code == 0 else err)

    print("\n=== Pods in kubeagents-system ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "get", "pods", "-n", NAMESPACE, "-o", "wide"])
    print(out if code == 0 else err)

    print("\n=== Operator Controller Manager Logs ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "logs", "-n", NAMESPACE, "-l", "control-plane=controller-manager", "--tail=50"])
    print(out if code == 0 else err)

    print("\n=== Pod Events ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "get", "events", "-n", NAMESPACE, "--sort-by=.metadata.creationTimestamp"])
    print(out if code == 0 else err)

if __name__ == "__main__":
    main()
