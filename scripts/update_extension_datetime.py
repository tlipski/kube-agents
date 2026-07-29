#!/usr/bin/env python3
"""
update_extension_datetime.py

Updates the test AgentExtension CR to output current time and date in its mock plugin,
re-applies the manifest to the ka-dev-mgmt cluster, triggers operator reconciliation,
and verifies the updated log output in the platform-agent container.
"""

import os
import subprocess
import time
import sys

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

updated_ext_manifest = f"""apiVersion: kubeagents.x-k8s.io/v1alpha1
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
      version: 1.1.0
      description: Mock Hello World Extension Plugin with Date and Time Output
    plugins/mock_hello_world/__init__.py: |
      import logging
      import threading
      import time
      import sys
      from datetime import datetime, timezone

      logger = logging.getLogger("hermes.plugin.mock_hello_world")

      def _loop():
          while True:
              now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
              msg = f"[MOCK-EXTENSION-PLUGIN] hello world - Current time: {{now_str}}"
              print(msg, file=sys.stderr, flush=True)
              logger.info(msg)
              time.sleep(10)

      def register(ctx=None):
          print("[MOCK-EXTENSION-PLUGIN] Registering Mock Extension Plugin with Date/Time...", file=sys.stderr, flush=True)
          logger.info("Registering Mock Extension Plugin with Date/Time...")
          t = threading.Thread(target=_loop, daemon=True)
          t.start()
"""

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    ext_file = "/tmp/test-agent-extension-datetime.yaml"
    with open(ext_file, "w") as f:
        f.write(updated_ext_manifest)

    print("=== Step 1: Applying updated AgentExtension CR with date/time output ===")
    res = subprocess.run(["kubectl", "--context", context_name, "apply", "-f", ext_file], capture_output=True, text=True, env=env)
    print("STDOUT:\n", res.stdout)
    if res.returncode != 0:
        print("STDERR:\n", res.stderr, file=sys.stderr)
        sys.exit(1)

    print("=== Step 2: Triggering PlatformAgent reconciliation ===")
    res = subprocess.run(["kubectl", "--context", context_name, "annotate", "platformagent", "platform-agent", "-n", NAMESPACE, f"reconcile-ts={int(time.time())}", "--overwrite"], capture_output=True, text=True, env=env)
    print("STDOUT:\n", res.stdout)

    print("=== Step 3: Waiting for platform-agent-gateway deployment rollout ===")
    time.sleep(5)
    subprocess.run(["kubectl", "--context", context_name, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=180s"], env=env)

    print("=== Step 4: Waiting 15s for plugin thread execution ===")
    time.sleep(15)

    pod_res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = pod_res.stdout.strip()
    print(f"Active pod name: {pod_name}\n")

    print("=== Step 5: Tailing container logs for date/time output ===")
    log_res = subprocess.run(["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=100"], capture_output=True, text=True, env=env)

    found = False
    for line in log_res.stdout.splitlines() + log_res.stderr.splitlines():
        if "MOCK-EXTENSION-PLUGIN" in line or "Current time:" in line:
            print("MATCH:", line)
            found = True

    if not found:
        print("\nFull STDOUT:\n", log_res.stdout)
        print("\nFull STDERR:\n", log_res.stderr)
        sys.exit(1)
    else:
        print("\n✓ SUCCESS: Verified date and time output from AgentExtension mock plugin!")

if __name__ == "__main__":
    main()
