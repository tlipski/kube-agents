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
        - platforms.mock_hello_world
  files:
    platforms/mock_hello_world.py: |
      import logging
      import threading
      import time
      import sys

      logger = logging.getLogger("hermes.plugin.mock_hello_world")

      def _loop():
          while True:
              print("[MOCK-EXTENSION-PLUGIN] hello world", file=sys.stderr, flush=True)
              logger.info("hello world")
              time.sleep(30)

      def register(ctx=None):
          print("[MOCK-EXTENSION-PLUGIN] Registering Mock Extension Plugin...", file=sys.stderr, flush=True)
          logger.info("Registering Mock Extension Plugin...")
          t = threading.Thread(target=_loop, daemon=True)
          t.start()
"""

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    ext_file = "/tmp/test-agent-extension-v3.yaml"
    with open(ext_file, "w") as f:
        f.write(ext_manifest)

    print("=== Step 1: Applying AgentExtension with platforms/mock_hello_world.py ===")
    subprocess.run(["kubectl", "--context", context_name, "apply", "-f", ext_file], check=True, env=env)

    print("=== Step 2: Triggering PlatformAgent reconciliation ===")
    subprocess.run(["kubectl", "--context", context_name, "annotate", "platformagent", "platform-agent", "-n", NAMESPACE, f"reconcile-ts={int(time.time())}", "--overwrite"], check=True, env=env)

    print("=== Step 3: Waiting 5s for rollout ===")
    time.sleep(5)
    subprocess.run(["kubectl", "--context", context_name, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=180s"], env=env)

    # Get active pod name
    pod_res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = pod_res.stdout.strip()
    print("Pod name:", pod_name)

    print("\n=== Step 4: Testing manual invocation of plugin register() inside container ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--", "/opt/hermes/.venv/bin/python3", "-c", "import sys; sys.path.insert(0, '/opt/hermes'); import plugins.platforms.mock_hello_world as m; m.register()"], capture_output=True, text=True, env=env)
    print(res.stdout, res.stderr)

    print("\n=== Step 5: Tailing pod logs ===")
    time.sleep(5)
    log_res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=50"], capture_output=True, text=True, env=env)
    print(log_res.stdout)
    print(log_res.stderr)

if __name__ == "__main__":
    main()
