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
        - mock_hello_world
  files:
    plugins/mock_hello_world/plugin.yaml: |
      name: mock_hello_world
      version: 1.0.0
      description: Mock Hello World Extension Plugin
    plugins/mock_hello_world/__init__.py: |
      import logging
      import threading
      import time
      import sys

      logger = logging.getLogger("hermes.plugin.mock_hello_world")

      def _loop():
          while True:
              print("[MOCK-EXTENSION-PLUGIN] hello world", file=sys.stderr, flush=True)
              logger.info("hello world")
              time.sleep(10)

      def register(ctx=None):
          print("[MOCK-EXTENSION-PLUGIN] Registering Mock Extension Plugin...", file=sys.stderr, flush=True)
          logger.info("Registering Mock Extension Plugin...")
          t = threading.Thread(target=_loop, daemon=True)
          t.start()
"""

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    ext_file = "/tmp/test-valid-agent-extension.yaml"
    with open(ext_file, "w") as f:
        f.write(ext_manifest)

    print("=== Step 1: Applying valid AgentExtension CR ===")
    subprocess.run(["kubectl", "--context", context_name, "apply", "-f", ext_file], check=True, env=env)

    print("=== Step 2: Triggering PlatformAgent reconciliation ===")
    subprocess.run(["kubectl", "--context", context_name, "annotate", "platformagent", "platform-agent", "-n", NAMESPACE, f"reconcile-ts={int(time.time())}", "--overwrite"], check=True, env=env)

    print("=== Step 3: Waiting 5s for rollout ===")
    time.sleep(5)
    subprocess.run(["kubectl", "--context", context_name, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=180s"], env=env)

    print("=== Step 4: Waiting 15s for plugin thread execution ===")
    time.sleep(15)

    pod_res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = pod_res.stdout.strip()
    print("Pod name:", pod_name)

    print("\n=== Step 5: Tailing container logs for [MOCK-EXTENSION-PLUGIN] hello world ===")
    log_res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=100"], capture_output=True, text=True, env=env)
    
    found = False
    for line in log_res.stdout.splitlines() + log_res.stderr.splitlines():
        if "MOCK-EXTENSION-PLUGIN" in line or "hello world" in line:
            print("FOUND LOG:", line)
            found = True

    if not found:
        print("\nFull STDOUT:\n", log_res.stdout)
        print("\nFull STDERR:\n", log_res.stderr)
    else:
        print("\nSUCCESS: Verified 'hello world' message in Hermes gateway logs!")

if __name__ == "__main__":
    main()
