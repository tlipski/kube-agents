#!/usr/bin/env python3
import subprocess
import os

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"
env["MODEL_PROVIDER"] = "gemini"
env["MODEL_DEFAULT_NAME"] = "gemini-2.5-flash-lite"
env["NAMESPACE"] = NAMESPACE

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print("Re-deploying LiteLLM with MODEL_DEFAULT_NAME=gemini-2.5-flash-lite...")
    subprocess.run(["make", "-C", "k8s-operator", "deploy-litellm"], cwd=REPO_DIR, check=True, env=env)
    
    subprocess.run(["kubectl", "--context", context_name, "rollout", "restart", "deployment/litellm", "-n", NAMESPACE], check=True, env=env)
    subprocess.run(["kubectl", "--context", context_name, "rollout", "status", "deployment/litellm", "-n", NAMESPACE, "--timeout=180s"], check=True, env=env)
    print("LiteLLM updated!")

if __name__ == "__main__":
    main()
