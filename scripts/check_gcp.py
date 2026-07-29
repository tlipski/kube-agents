#!/usr/bin/env python3
import subprocess
import os

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def run_cmd(cmd):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL, env=env)
    return res.stdout.strip(), res.stderr.strip(), res.returncode

def main():
    out, err, code = run_cmd(["gcloud", "org-policies", "list", "--project=tomeklipski-izrhgv", "--format=json"])
    print("Org policies:\n", out if code == 0 else err)

if __name__ == "__main__":
    main()
