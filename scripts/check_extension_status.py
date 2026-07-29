#!/usr/bin/env python3
"""
check_extension_status.py

A script to inspect the AgentExtension CRD, active AgentExtension CRs, generated ConfigMaps,
and container logs on the ka-dev-mgmt cluster.

Usage:
  python3 scripts/check_extension_status.py
"""

import os
import subprocess
import sys

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd, description):
    print(f"\n=======================================================")
    print(f"=== {description} ===")
    print(f"=======================================================")
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr)

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print(f"Connecting to cluster '{MGMT_CLUSTER}' in region '{REGION}'...")
    subprocess.run(["gcloud", "container", "clusters", "get-credentials", MGMT_CLUSTER, "--region", REGION, "--project", PROJECT_ID], capture_output=True, env=env)

    # 1. AgentExtension CRD Summary & Definition
    run_cmd(
        ["kubectl", "--context", context_name, "get", "crd", "agentextensions.kubeagents.x-k8s.io"],
        "1. AgentExtension CRD Overview"
    )

    # 2. AgentExtension Custom Resources in namespace
    run_cmd(
        ["kubectl", "--context", context_name, "get", "agentextension", "-n", NAMESPACE, "-o", "yaml"],
        "2. Applied AgentExtension Custom Resources"
    )

    # 3. Generated Extensions ConfigMap
    run_cmd(
        ["kubectl", "--context", context_name, "get", "cm", "platform-agent-extensions", "-n", NAMESPACE, "-o", "yaml"],
        "3. Generated platform-agent-extensions ConfigMap"
    )

    # 4. Operator Controller Logs
    run_cmd(
        ["kubectl", "--context", context_name, "logs", "-n", NAMESPACE, "-l", "control-plane=controller-manager", "--tail=30"],
        "4. Operator Controller Manager Logs"
    )

    # 5. Platform Agent Gateway Pod Container Logs
    pod_res = subprocess.run(
        ["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, env=env
    )
    pod_name = pod_res.stdout.strip()
    if pod_name:
        run_cmd(
            ["kubectl", "--context", context_name, "logs", pod_name, "-c", "platform-agent", "-n", NAMESPACE, "--tail=50"],
            f"5. Platform Agent Gateway Container Logs ({pod_name})"
        )
    else:
        print("\nNo running platform-agent-gateway pod found.")

if __name__ == "__main__":
    main()
