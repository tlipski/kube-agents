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
    print(f"Setting spec.harness.projectId={PROJECT_ID} on PlatformAgent platform-agent...")
    patch_json = f'{{"spec":{{"harness":{{"projectId":"{PROJECT_ID}"}}}}}}'
    res = subprocess.run(["kubectl", "--context", context_name, "patch", "platformagent", "platform-agent", "-n", NAMESPACE, "--type=merge", "-p", patch_json], capture_output=True, text=True, env=env)
    print("STDOUT:\n", res.stdout)
    print("STDERR:\n", res.stderr)

if __name__ == "__main__":
    main()
