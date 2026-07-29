#!/usr/bin/env python3
import subprocess
import os
import json

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    
    # Get pod name
    res = subprocess.run(["kubectl", "--context", context_name, "get", "pod", "-n", NAMESPACE, "-l", "app=platform-agent-gateway", "-o", "jsonpath={.items[0].metadata.name}"], capture_output=True, text=True, env=env)
    pod_name = res.stdout.strip()
    print("Pod:", pod_name)

    print("=== Testing LiteLLM /health/liveliness ===")
    res = subprocess.run(["kubectl", "--context", context_name, "exec", "-n", NAMESPACE, pod_name, "-c", "platform-agent", "--", "curl", "-s", "http://litellm:80/health/liveliness"], capture_output=True, text=True, env=env)
    print("Health response:", res.stdout, res.stderr)

    print("=== Testing LiteLLM /v1/chat/completions directly ===")
    payload = {
        "model": "model-default",
        "messages": [{"role": "user", "content": "Hello, answer in 5 words."}]
    }
    payload_json = json.dumps(payload)

    curl_chat = [
        "kubectl", "--context", context_name, "exec", "-n", NAMESPACE, pod_name, "-c", "platform-agent", "--",
        "curl", "-s", "-m", "30", "-X", "POST", "http://litellm:80/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "-d", payload_json
    ]
    res = subprocess.run(curl_chat, capture_output=True, text=True, env=env)
    print("Chat completions response:\n", res.stdout)
    if res.stderr:
        print("STDERR:\n", res.stderr)

if __name__ == "__main__":
    main()
