#!/usr/bin/env python3
import subprocess
import os
import sys

PROJECT_ID = "tomeklipski-izrhgv"
REGION = "europe-west3"
CLUSTERS = ["ka-dev-mgmt", "ka-dev-cluster1"]

env = os.environ.copy()
env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

def main():
    results = {}
    processes = []
    for c in CLUSTERS:
        cmd = [
            "gcloud", "beta", "container", "clusters", "create", c,
            "--region", REGION,
            "--machine-type", "e2-standard-4",
            "--num-nodes", "1",
            "--workload-pool", f"{PROJECT_ID}.svc.id.goog",
            "--managed-otel-scope", "COLLECTION_AND_INSTRUMENTATION_COMPONENTS",
            "--project", PROJECT_ID,
            "--quiet"
        ]
        print(f"Starting creation of cluster {c} in {REGION}...")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        processes.append((c, p))

    for c, p in processes:
        stdout, stderr = p.communicate()
        print(f"Cluster {c} creation finished with code {p.returncode}:")
        print("STDOUT:\n", stdout)
        print("STDERR:\n", stderr)
        results[c] = p.returncode

    if all(code == 0 for code in results.values()):
        print("All clusters created successfully!")
        sys.exit(0)
    else:
        print("Error creating clusters:", results)
        sys.exit(1)

if __name__ == "__main__":
    main()
