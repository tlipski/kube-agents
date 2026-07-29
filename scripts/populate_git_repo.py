#!/usr/bin/env python3
import subprocess
import os
import shutil

PAT = os.environ.get("GITHUB_PAT")
if not PAT:
    print("Error: GITHUB_PAT environment variable not set")
    exit(1)

REPO_URL = f"https://x-access-token:{PAT}@github.com/tlipski/ka-dev-cluster1.git"
WORK_DIR = "/tmp/ka-dev-cluster1-repo"

if os.path.exists(WORK_DIR):
    shutil.rmtree(WORK_DIR)

os.makedirs(WORK_DIR)

# Copy repo structure
src_dir = "/usr/local/google/home/tomeklipski/d/ka-dev"

for item in ["README.md", "AGENTS.md", "INSTALL.md", "agents", "deploy", "k8s-operator", "docs"]:
    src_path = os.path.join(src_dir, item)
    dst_path = os.path.join(WORK_DIR, item)
    if os.path.isdir(src_path):
        shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns(".git"))
    elif os.path.isfile(src_path):
        shutil.copy2(src_path, dst_path)

# Initialize git repository
def run_git(cmd, cwd=WORK_DIR):
    res = subprocess.run(["git"] + cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Git command {' '.join(cmd)} failed:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
    return res.returncode

run_git(["init"])
run_git(["config", "user.name", "Platform Agent Bot"])
run_git(["config", "user.email", "platform-agent@ka-dev.internal"])
run_git(["checkout", "-b", "main"])
run_git(["add", "."])
run_git(["commit", "-m", "feat: populate main branch with IaC configurations for ka-dev-cluster1"])
run_git(["remote", "add", "origin", REPO_URL])

print("Pushing to main branch...")
res = run_git(["push", "-u", "origin", "main", "--force"])

if res == 0:
    print("Successfully populated and pushed main branch to https://github.com/tlipski/ka-dev-cluster1!")
else:
    print("Failed to push to GitHub repository.")
    exit(1)
