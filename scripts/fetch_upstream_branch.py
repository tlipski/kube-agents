#!/usr/bin/env python3
import subprocess
import os
import shutil

PAT = os.environ.get("GITHUB_PAT", "")
if PAT:
    REPO_URL = f"https://x-access-token:{PAT}@github.com/tlipski/ka.git"
else:
    REPO_URL = "git@github.com:tlipski/ka.git"

TARGET_DIR = "/tmp/ka-upstream"

if os.path.exists(TARGET_DIR):
    shutil.rmtree(TARGET_DIR)

def main():
    print(f"Cloning {REPO_URL} (branch: feature/modular-agents)...")
    res = subprocess.run(["git", "clone", "--single-branch", "--branch", "feature/modular-agents", REPO_URL, TARGET_DIR], capture_output=True, text=True)
    if res.returncode != 0:
        print("HTTPS clone failed, trying git@github.com:tlipski/ka.git...")
        res = subprocess.run(["git", "clone", "--single-branch", "--branch", "feature/modular-agents", "git@github.com:tlipski/ka.git", TARGET_DIR], capture_output=True, text=True)
    
    print("STDOUT:\n", res.stdout)
    print("STDERR:\n", res.stderr)
    if res.returncode == 0:
        print("Successfully cloned feature/modular-agents!")

if __name__ == "__main__":
    main()
