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

    print("\n=== Finding mock_hello_world.py inside pod ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "find", "/opt", "-name", "mock_hello_world.py"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Testing python import in pod venv ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "/opt/hermes/.venv/bin/python3", "-c", "import sys; sys.path.insert(0, '/opt/data'); sys.path.insert(0, '/opt/data/plugins'); import mock_hello_world; print('Successfully imported:', mock_hello_world)"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

if __name__ == "__main__":
    main()
