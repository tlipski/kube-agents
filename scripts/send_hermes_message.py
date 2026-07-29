#!/usr/bin/env python3
"""
send_hermes_message.py

A script to send messages to the Hermes Chat API running on the ka-dev-mgmt cluster.

Usage:
  python3 scripts/send_hermes_message.py "Hello Hermes, report fleet status."
"""

import sys
import os
import json
import subprocess

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
DEFAULT_KEY = "hermes-secret-key-12345"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def send_message(prompt: str, api_key: str = None) -> str:
    if not api_key:
        api_key = os.environ.get("API_SERVER_KEY", DEFAULT_KEY)

    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"

    # Get active platform-agent-gateway pod
    res = subprocess.run(
        ["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, env=env
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise Exception(f"Failed to find running platform-agent-gateway pod: {res.stderr}")

    pod_name = res.stdout.strip()

    payload = {
        "model": "hermes-agent",
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    curl_cmd = [
        "kubectl", "--context", context_name, "exec", "-n", NAMESPACE, pod_name, "-c", "platform-agent", "--",
        "curl", "-s", "-X", "POST", "http://localhost:8642/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {api_key}",
        "-d", json.dumps(payload)
    ]

    res = subprocess.run(curl_cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise Exception(f"kubectl exec failed: {res.stderr}")

    try:
        data = json.loads(res.stdout)
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0]["message"]["content"]
        else:
            return f"Raw Response:\n{res.stdout}"
    except json.JSONDecodeError:
        return f"Raw Output:\n{res.stdout}"

def main():
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = "Hello Hermes! Please provide a brief status update on your harness configuration and active GKE fleet."

    print(f"Sending message to Hermes Chat API on cluster '{MGMT_CLUSTER}'...")
    print(f"Prompt: \"{prompt}\"\n")

    try:
        reply = send_message(prompt)
        print("=== Hermes Response ===")
        print(reply)
        print("=======================")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
