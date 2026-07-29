#!/usr/bin/env python3
import subprocess
import os
import datetime

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west1"
MGMT_CLUSTER = "ka-dev-mgmt"
NAMESPACE = "kubeagents-system"
REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
IMAGE_TAG = f"gcr.io/{PROJECT_ID}/k8s-operator:v{timestamp}"

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd, cwd=REPO_DIR):
    print(f"Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    print(f"STDOUT:\n{res.stdout}")
    if res.returncode != 0:
        print(f"STDERR:\n{res.stderr}")
        raise Exception(f"Command failed: {' '.join(cmd)}")
    return res

docker_config_dir = "/tmp/docker_config_operator"
os.makedirs(docker_config_dir, exist_ok=True)
with open(os.path.join(docker_config_dir, "config.json"), "w") as f:
    f.write('{"auths": {}}')
env["DOCKER_CONFIG"] = docker_config_dir

def main():
    context_name = f"gke_{PROJECT_ID}_{REGION}_{MGMT_CLUSTER}"
    print(f"=== Tagging Operator Image as {IMAGE_TAG} ===")

    print("=== Step 1: Configuring Docker authentication for GCR ===")
    adc_token = subprocess.run(["gcloud", "auth", "application-default", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["docker", "login", "-u", "oauth2accesstoken", "--password-stdin", "https://gcr.io"], input=adc_token, text=True, check=True, env=env)

    print("=== Step 2: Building Operator Docker Image ===")
    run_cmd(["docker", "build", "-t", IMAGE_TAG, "-f", "Dockerfile", "."], cwd=os.path.join(REPO_DIR, "k8s-operator"))

    print("=== Step 3: Pushing Operator Docker Image ===")
    run_cmd(["docker", "push", IMAGE_TAG])

    print("=== Step 4: Installing/Updating CRDs and RBAC roles in cluster ===")
    run_cmd(["kubectl", "--context", context_name, "apply", "--server-side", "-f", "config/crd/bases"], cwd=os.path.join(REPO_DIR, "k8s-operator"))
    run_cmd(["kubectl", "--context", context_name, "apply", "--server-side", "-f", "config/rbac/role.yaml"], cwd=os.path.join(REPO_DIR, "k8s-operator"))

    print("=== Step 5: Updating Operator Deployment Image in cluster ===")
    run_cmd(["kubectl", "--context", context_name, "set", "image", "deployment/kubeagents-controller-manager", f"manager={IMAGE_TAG}", "-n", NAMESPACE])
    run_cmd(["kubectl", "--context", context_name, "rollout", "status", "deployment/kubeagents-controller-manager", "-n", NAMESPACE, "--timeout=180s"])

    print(f"Operator updated and deployed with image tag {IMAGE_TAG} successfully!")

if __name__ == "__main__":
    main()
