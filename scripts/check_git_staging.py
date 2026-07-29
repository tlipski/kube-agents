#!/usr/bin/env python3
import subprocess
import os

REPO_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def main():
    print("=== Git status porcelain ===")
    res = subprocess.run(["git", "status", "-s"], cwd=REPO_DIR, capture_output=True, text=True)
    print(res.stdout)

if __name__ == "__main__":
    main()
