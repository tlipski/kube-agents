#!/usr/bin/env python3
"""
compare_extension_changes.py

A script to compare files between the current repository and the upstream
git@github.com:tlipski/ka.git feature/modular-agents branch to verify ported k8s-operator extension files.

Usage:
  python3 scripts/compare_extension_changes.py
"""

import subprocess
import os
import sys

UPSTREAM_DIR = "/tmp/ka-upstream"
LOCAL_DIR = "/usr/local/google/home/tomeklipski/d/ka-dev"

def ensure_upstream_cloned():
    if not os.path.exists(UPSTREAM_DIR):
        print(f"Cloning upstream repo into {UPSTREAM_DIR}...")
        pat = os.environ.get("GITHUB_PAT", "")
        repo_url = f"https://x-access-token:{pat}@github.com/tlipski/ka.git" if pat else "git@github.com:tlipski/ka.git"
        res = subprocess.run(["git", "clone", "--single-branch", "--branch", "feature/modular-agents", repo_url, UPSTREAM_DIR], capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["git", "clone", "--single-branch", "--branch", "feature/modular-agents", "git@github.com:tlipski/ka.git", UPSTREAM_DIR], capture_output=True, text=True)
        if res.returncode != 0:
            print("Failed to clone upstream repository:", res.stderr, file=sys.stderr)
            sys.exit(1)

extension_files = [
    "k8s-operator/api/v1alpha1/agentextension_types.go",
    "k8s-operator/api/v1alpha1/common_types.go",
    "k8s-operator/api/v1alpha1/zz_generated.deepcopy.go",
    "k8s-operator/config/crd/bases/kubeagents.x-k8s.io_agentextensions.yaml",
    "k8s-operator/config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml",
    "k8s-operator/config/default/kustomization.yaml",
    "k8s-operator/config/rbac/role.yaml",
    "k8s-operator/internal/controller/platformagent_controller.go",
    "k8s-operator/internal/controller/platformagent_manifests.go",
    "k8s-operator/internal/controller/manifest_helpers.go",
    "k8s-operator/internal/controller/platformagent_manifests_test.go",
    "k8s-operator/internal/webhook/platformagent_webhook.go",
    "k8s-operator/scripts/platform-agent.yaml.template",
]

def main():
    ensure_upstream_cloned()
    print("=== Comparison Report: Local vs Upstream feature/modular-agents ===")
    
    differing = []
    matching = []
    missing = []

    for rel in extension_files:
        local_path = os.path.join(LOCAL_DIR, rel)
        upstream_path = os.path.join(UPSTREAM_DIR, rel)

        if not os.path.exists(local_path):
            missing.append((rel, "Missing locally"))
            continue
        if not os.path.exists(upstream_path):
            missing.append((rel, "Missing upstream"))
            continue

        res = subprocess.run(["diff", "-q", local_path, upstream_path], capture_output=True, text=True)
        if res.returncode == 0:
            matching.append(rel)
        else:
            differing.append(rel)

    print(f"\n✓ Identical/Verified Files ({len(matching)}):")
    for f in matching:
        print(f"  [MATCH] {f}")

    if differing:
        print(f"\n! Files with Diffs ({len(differing)}):")
        for f in differing:
            print(f"  [DIFF]  {f}")

    if missing:
        print(f"\n? Missing Files ({len(missing)}):")
        for f, reason in missing:
            print(f"  [MISS]  {f} ({reason})")

    if not differing and not missing:
        print("\nSUCCESS: All k8s-operator extension files match the upstream feature/modular-agents branch perfectly!")
    else:
        print("\nNote: Differences were detected in above files.")

if __name__ == "__main__":
    main()
