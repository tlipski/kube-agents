#!/usr/bin/env python3
import subprocess
import os
import sys
import json
import time

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
API_SERVER_KEY = "hermes-secret-key-12345"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print(f"Testing Hermes Chat API end-to-end on cluster {MGMT_CLUSTER}...")

    # Get platform-agent-gateway pod name
    res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    if res.returncode != 0 or not res.stdout.strip():
        print("Error: Could not find platform-agent-gateway pod.")
        sys.exit(1)
    
    pod_name = res.stdout.strip()
    print(f"Target Pod: {pod_name}")

    # Prepare HTTP request to test LLM connectivity via Hermes Chat API
    payload = {
        "model": "hermes-agent",
        "messages": [
            {"role": "user", "content": "Hello Hermes! Please reply with 'Hermes LLM connectivity verified successfully!' and state your cluster name."}
        ]
    }

    payload_json = json.dumps(payload)
    curl_cmd = [
        "kubectl", "--context", context_name, "exec", "-n", NAMESPACE, pod_name, "-c", "platform-agent", "--",
        "curl", "-s", "-X", "POST", "http://localhost:8642/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {API_SERVER_KEY}",
        "-d", payload_json
    ]

    print("Executing request to Hermes Chat API (/v1/chat/completions)...")
    res = subprocess.run(curl_cmd, capture_output=True, text=True, env=env)
    print("STDOUT response:\n", res.stdout)
    if res.stderr:
        print("STDERR:\n", res.stderr)

    try:
        data = json.loads(res.stdout)
        if "choices" in data and len(data["choices"]) > 0:
            msg_content = data["choices"][0]["message"]["content"]
            print("\n=== LLM Response Received ===")
            print(msg_content)
            print("=============================")
            print("\n✓ E2E LLM Connectivity Test PASSED!")
            sys.exit(0)
        else:
            print("\n✗ Response did not contain expected choices field:", data)
            sys.exit(1)
    except Exception as e:
        print(f"\nFailed to parse response JSON: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
