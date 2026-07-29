#!/usr/bin/env python3
import subprocess
import os
import sys
import time

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west1"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd, cwd=REPO_DIR, check=True):
    print(f"Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    print(f"STDOUT:\n{res.stdout}")
    if res.returncode != 0:
        print(f"STDERR:\n{res.stderr}")
        if check:
            raise Exception(f"Command failed with exit code {res.returncode}")
    return res

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print(f"=== Step 1: Connecting to {MGMT_CLUSTER} ===")
    run_cmd(["gcloud", "container", "clusters", "get-credentials", MGMT_CLUSTER, "--region", REGION, "--project", PROJECT_ID])

    print("=== Step 2: Building and Pushing Operator Image ===")
    version = int(time.time())
    img = f"gcr.io/{PROJECT_ID}/k8s-operator:v{version}"
    run_cmd(["gcloud", "auth", "configure-docker", "gcr.io"])
    run_cmd(["make", "-C", "k8s-operator", "docker-build", f"IMG={img}"])
    run_cmd(["make", "-C", "k8s-operator", "docker-push", f"IMG={img}"])

    print("=== Step 3: Re-deploying Operator Controller Manager ===")
    run_cmd(["make", "-C", "k8s-operator", "deploy", f"IMG={img}"])
    run_cmd(["kubectl", "--context", context_name, "rollout", "restart", "deployment/kubeagents-controller-manager", "-n", NAMESPACE])
    run_cmd(["kubectl", "--context", context_name, "rollout", "status", "deployment/kubeagents-controller-manager", "-n", NAMESPACE, "--timeout=180s"])

    print("=== Step 4: Applying Test AgentExtension manifest ===")
    ext_manifest = f"""apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentExtension
metadata:
  name: test-hello-world-extension
  namespace: {NAMESPACE}
spec:
  agentRef: platform-agent
  config: |
    plugins:
      enabled:
        - hermes_otel
        - session_store
        - session_otel_bridge
        - tool_call_audit
        - platforms.mock_hello_world
  files:
    platforms/mock_hello_world/plugin.yaml: |
      name: platforms.mock_hello_world
      version: 1.1.0
      description: Mock Hello World Extension Plugin
    platforms/mock_hello_world/__init__.py: |
      import logging
      import threading
      import time
      import sys
      from datetime import datetime

      logger = logging.getLogger("hermes.plugin.mock_hello_world")

      def _loop():
          while True:
              print(f"[MOCK-EXTENSION-PLUGIN] hello worldDDDDDDDDDDD {{datetime.now()}}", file=sys.stderr, flush=True)
              logger.info("hello world")
              time.sleep(30)

      def register(ctx=None):
          print("[MOCK-EXTENSION-PLUGIN] Registering Mock Extension Plugin...", file=sys.stderr, flush=True)
          logger.info("Registering Mock Extension Plugin...")
          t = threading.Thread(target=_loop, daemon=True)
          t.start()
"""
    ext_file = "/tmp/test-agent-extension.yaml"
    with open(ext_file, "w") as f:
        f.write(ext_manifest)

    run_cmd(["kubectl", "--context", context_name, "apply", "-f", ext_file])

    print("=== Step 5: Triggering PlatformAgent Gateway sync & rollout ===")
    time.sleep(3)
    # Restart deployment to ensure immediate pod creation with new extension config
    run_cmd(["kubectl", "--context", context_name, "rollout", "restart", "deployment/platform-agent-gateway", "-n", NAMESPACE], check=False)
    run_cmd(["kubectl", "--context", context_name, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=300s"], check=False)

    print("=== Step 6: Verifying AgentExtension CR status ===")
    run_cmd(["kubectl", "--context", context_name, "get", "agentextension", "test-hello-world-extension", "-n", NAMESPACE, "-o", "yaml"])

    print("=== Step 7: Verifying 'hello world' in Gateway container logs ===")
    time.sleep(15)  # wait for plugin loop trigger
    pod_res = run_cmd(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"])
    pod_name = pod_res.stdout.strip()
    
    # We grep for our custom log message to verify it's running
    run_cmd(["kubectl", "--context", context_name, "logs", pod_name, "-n", NAMESPACE], check=False)

if __name__ == "__main__":
    main()
