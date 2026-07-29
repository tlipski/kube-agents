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

    print("\n=== Listing /opt/hermes directory structure ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "ls", "-la", "/opt/hermes"], capture_output=True, text=True, env=env)
    print(res.stdout)

    print("\n=== Inspecting Hermes plugin loader code inside pod ===")
    py_script = """
import sys
import importlib

print('Python sys.path:', sys.path)

try:
    import gateway.plugins as gp
    print('gateway.plugins file:', getattr(gp, '__file__', None))
except Exception as e:
    print('gateway.plugins import error:', e)

try:
    from gateway.config import load_config
    cfg = load_config()
    print('Loaded config plugins:', cfg.get('plugins'))
except Exception as e:
    print('load_config error:', e)
"""
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "/opt/hermes/.venv/bin/python3", "-c", py_script], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

if __name__ == "__main__":
    main()
