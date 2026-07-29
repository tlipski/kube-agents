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

    print("\n=== Listing /opt/hermes/gateway ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "ls", "-la", "/opt/hermes/gateway"], capture_output=True, text=True, env=env)
    print(res.stdout)

    print("\n=== Searching for plugin loading code in /opt/hermes ===")
    py_script = """
import os

for root, dirs, files in os.walk('/opt/hermes'):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath) as f:
                    content = f.read()
                    if 'plugins' in content or 'plugin' in content:
                        if 'load' in content or 'import' in content:
                            if 'enabled' in content:
                                print('Candidate plugin loader file:', filepath)
            except Exception:
                pass
"""
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "/opt/hermes/.venv/bin/python3", "-c", py_script], capture_output=True, text=True, env=env)
    print(res.stdout)

if __name__ == "__main__":
    main()
