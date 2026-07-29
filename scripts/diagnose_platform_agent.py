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
    
    print("=== Describe PlatformAgent ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "describe", "platformagent", "platform-agent", "-n", NAMESPACE])
    print(out if code == 0 else err)

    print("\n=== Describe Deployment platform-agent-gateway ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "describe", "deployment", "platform-agent-gateway", "-n", NAMESPACE])
    print(out if code == 0 else err)

    print("\n=== Get ReplicaSets in kubeagents-system ===")
    out, err, code = run_cmd(["kubectl", "--context", context_name, "get", "rs", "-n", NAMESPACE])
    print(out if code == 0 else err)

if __name__ == "__main__":
    main()
