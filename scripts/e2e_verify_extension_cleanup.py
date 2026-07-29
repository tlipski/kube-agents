#!/usr/bin/env python3
"""
E2E verification script for AgentExtension resource cleanup, config limiting, and file path enforcement.
Executes against ka-dev-mgmt cluster.
"""

import subprocess
import os
import json
import time

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west1"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
CONTEXT = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd, check=True):
    print(f"Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and res.returncode != 0:
        print(f"STDOUT:\n{res.stdout}")
        print(f"STDERR:\n{res.stderr}")
        raise Exception(f"Command failed: {' '.join(cmd)}")
    return res

def get_gateway_pod(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        res = run_cmd(["kubectl", "--context", CONTEXT, "get", "pods", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "--field-selector=status.phase=Running", "-o", "jsonpath={.items[0].metadata.name}"], check=False)
        pod = res.stdout.strip()
        if pod:
            print(f"Found active gateway pod: {pod}")
            return pod
        time.sleep(3)
    raise Exception("Timed out waiting for running platform-agent-gateway pod")

def get_config_map_yaml():
    res = run_cmd(["kubectl", "--context", CONTEXT, "get", "cm", "platform-agent-config", "-n", NAMESPACE, "-o", "json"])
    data = json.loads(res.stdout)
    return data.get("data", {}).get("config.yaml", "")

def get_extensions_config_map_data():
    res = run_cmd(["kubectl", "--context", CONTEXT, "get", "cm", "platform-agent-extensions", "-n", NAMESPACE, "-o", "json"], check=False)
    if res.returncode != 0:
        return {}
    data = json.loads(res.stdout)
    return data.get("data", {})

def test_requirement_2_config_limiting():
    print("\n=== Testing Requirement 2: Limit Config Fields to 'platforms' Subkey Only ===")
    ext_yaml = """
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentExtension
metadata:
  name: test-req2-config-limiting
  namespace: kubeagents-system
spec:
  config: |
    model:
      default: "malicious-hacked-model"
    platforms:
      pubsub:
        enabled: true
        test_key: "valid_req2_value"
    terminal:
      backend: "disallowed_backend"
"""
    manifest_file = "/tmp/test_req2.yaml"
    with open(manifest_file, "w") as f:
        f.write(ext_yaml)
    
    try:
        run_cmd(["kubectl", "--context", CONTEXT, "apply", "-f", manifest_file])
        time.sleep(5)
        
        config_content = get_config_map_yaml()
        
        print("Checking rendered config.yaml...")
        if "malicious-hacked-model" in config_content:
            raise Exception("FAILED: 'model' subkey was illegally merged from extension config!")
        if "disallowed_backend" in config_content:
            raise Exception("FAILED: 'terminal' subkey was illegally merged from extension config!")
        if "valid_req2_value" not in config_content:
            raise Exception("FAILED: 'platforms' subkey was NOT merged into config.yaml!")
        
        print("PASSED: Requirement 2 config limiting verified successfully!")
    finally:
        run_cmd(["kubectl", "--context", CONTEXT, "delete", "-f", manifest_file, "--ignore-not-found"], check=False)

def test_requirement_3_path_limiting():
    print("\n=== Testing Requirement 3: Limit File Paths to skills/ and plugins/ Only ===")
    ext_yaml = """
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentExtension
metadata:
  name: test-req3-path-limiting
  namespace: kubeagents-system
spec:
  files:
    "skills/valid-skill-req3/SKILL.md": "valid skill content"
    "plugins/valid-plugin-req3/main.py": "valid plugin content"
    "prompts/disallowed_prompt.txt": "disallowed prompt content"
    "platforms/disallowed_plat/config.yaml": "disallowed platform content"
    "../../../../etc/passwd": "disallowed traversal content"
"""
    manifest_file = "/tmp/test_req3.yaml"
    with open(manifest_file, "w") as f:
        f.write(ext_yaml)
    
    try:
        run_cmd(["kubectl", "--context", CONTEXT, "apply", "-f", manifest_file])
        time.sleep(5)
        
        cm_data = get_extensions_config_map_data()
        
        print(f"Extensions ConfigMap keys: {list(cm_data.keys())}")
        if "skills___valid-skill-req3___SKILL.md" not in cm_data:
            raise Exception("FAILED: skills/valid-skill-req3/SKILL.md was missing from ConfigMap!")
        if "plugins___valid-plugin-req3___main.py" not in cm_data:
            raise Exception("FAILED: plugins/valid-plugin-req3/main.py was missing from ConfigMap!")
        
        # Check that disallowed keys are absent
        for key in cm_data:
            if "prompts" in key or "disallowed" in key or "passwd" in key:
                raise Exception(f"FAILED: Disallowed file key '{key}' was illegally created in ConfigMap!")
        
        print("PASSED: Requirement 3 path limiting verified successfully!")
    finally:
        run_cmd(["kubectl", "--context", CONTEXT, "delete", "-f", manifest_file, "--ignore-not-found"], check=False)

def test_requirement_1_resource_cleanup():
    print("\n=== Testing Requirement 1: Resource Cleanup & Revert on Removal ===")
    ext_yaml = """
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: AgentExtension
metadata:
  name: test-req1-cleanup
  namespace: kubeagents-system
spec:
  config: |
    platforms:
      pubsub:
        enabled: true
        req1_test_marker: "active_req1_marker"
  files:
    "skills/e2e-cleanup-test-skill/SKILL.md": "e2e cleanup test skill content"
"""
    manifest_file = "/tmp/test_req1.yaml"
    with open(manifest_file, "w") as f:
        f.write(ext_yaml)
    
    try:
        print("1. Applying AgentExtension resource...")
        run_cmd(["kubectl", "--context", CONTEXT, "apply", "-f", manifest_file])
        
        print("Waiting for pod rollout after extension creation...")
        run_cmd(["kubectl", "--context", CONTEXT, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=120s"], check=False)
        time.sleep(5)
        
        pod = get_gateway_pod()
        
        # Verify config marker in ConfigMap
        config_content = get_config_map_yaml()
        if "req1_test_marker" not in config_content:
            raise Exception("FAILED: Extension config marker not found in platform-agent-config ConfigMap!")
        print("Verified extension config marker is active in config.yaml.")
        
        # Verify skill file deployed in pod filesystem
        exec_res = run_cmd(["kubectl", "--context", CONTEXT, "exec", pod, "-n", NAMESPACE, "-c", "platform-agent", "--", "cat", "/opt/data/skills/e2e-cleanup-test-skill/SKILL.md"])
        if "e2e cleanup test skill content" not in exec_res.stdout:
            raise Exception("FAILED: Deployed file not found in gateway pod filesystem!")
        print("Verified deployed skill file exists on pod filesystem.")
        
        print("\n2. Deleting AgentExtension resource...")
        run_cmd(["kubectl", "--context", CONTEXT, "delete", "-f", manifest_file])
        
        print("Waiting for pod rollout after extension deletion...")
        time.sleep(5)
        run_cmd(["kubectl", "--context", CONTEXT, "rollout", "status", "deployment/platform-agent-gateway", "-n", NAMESPACE, "--timeout=120s"], check=False)
        time.sleep(5)
        
        pod_after = get_gateway_pod()
        
        # Verify config marker is reverted
        config_after = get_config_map_yaml()
        if "req1_test_marker" in config_after:
            raise Exception("FAILED: Extension config marker was NOT reverted after extension deletion!")
        print("Verified extension config changes successfully reverted in config.yaml.")
        
        # Verify skill file is removed from pod filesystem
        exec_after = run_cmd(["kubectl", "--context", CONTEXT, "exec", pod_after, "-n", NAMESPACE, "-c", "platform-agent", "--", "ls", "-la", "/opt/data/skills/e2e-cleanup-test-skill/SKILL.md"], check=False)
        if exec_after.returncode == 0:
            raise Exception("FAILED: Deployed file STILL exists on pod filesystem after extension deletion!")
        print("Verified deployed file successfully removed from pod filesystem!")
        
        print("PASSED: Requirement 1 resource cleanup and revert verified successfully!")
    finally:
        run_cmd(["kubectl", "--context", CONTEXT, "delete", "-f", manifest_file, "--ignore-not-found"], check=False)

def main():
    print(f"=== Starting E2E Verification on Cluster {MGMT_CLUSTER} ({CONTEXT}) ===")
    test_requirement_2_config_limiting()
    test_requirement_3_path_limiting()
    test_requirement_1_resource_cleanup()
    print("\n=======================================================")
    print("ALL E2E VERIFICATION TESTS PASSED SUCCESSFULLY 100%!")
    print("=======================================================")

if __name__ == "__main__":
    main()
