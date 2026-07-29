#!/usr/bin/env python3
"""
Master local E2E verification suite runner for Hermes sessions & K8s Operator.

This runner executes:
1. Go Operator E2E tests (`go test -v ./internal/testing -run TestHermes`)
2. Go Event Watcher tests (`go test -v ./cmd/k8s-event-watcher/...`)
3. Python Hermes Session & Operator E2E test suite (`tests/test_hermes_operator_e2e.py`)
4. Python PubSub Adapter & Extension Unit Tests (`tests/test_pubsub_adapter.py`)
5. Python PubSub Extension Session E2E Tests (`tests/test_pubsub_e2e.py`)
6. Python Platform Agent Skills Integrity tests (`tests/test_platform_skills_integrity.py`)

No live GKE cluster or cloud credentials required.
"""

import os
import sys
import time
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
K8S_OPERATOR_DIR = os.path.join(REPO_ROOT, "k8s-operator")


def run_command(cmd: list, cwd: str) -> tuple:
    start = time.time()
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    duration = time.time() - start
    return res.returncode, res.stdout, duration


def main():
    print("=======================================================================")
    print("🚀 Running Local Verification Suite (Hermes Sessions & K8s Operator)")
    print("=======================================================================\n")

    test_steps = [
        {
            "name": "Go Operator & Hermes Controller E2E Tests",
            "cmd": ["go", "test", "-v", "./internal/testing", "-run", "TestHermes"],
            "cwd": K8S_OPERATOR_DIR,
        },
        {
            "name": "Go k8s-event-watcher Unit & Integration Tests",
            "cmd": ["go", "test", "-v", "./cmd/k8s-event-watcher/..."],
            "cwd": K8S_OPERATOR_DIR,
        },
        {
            "name": "Python Hermes Session & Operator E2E Suite",
            "cmd": [sys.executable, "-m", "unittest", "-v", "tests/test_hermes_operator_e2e.py"],
            "cwd": REPO_ROOT,
        },
        {
            "name": "Python PubSub Adapter Unit Tests",
            "cmd": [sys.executable, "-m", "unittest", "-v", "tests/test_pubsub_adapter.py"],
            "cwd": REPO_ROOT,
        },
        {
            "name": "Python PubSub E2E Session Tests",
            "cmd": [sys.executable, "-m", "unittest", "-v", "tests/test_pubsub_e2e.py"],
            "cwd": REPO_ROOT,
        },
        {
            "name": "Python Platform Agent Skills Integrity Tests",
            "cmd": [sys.executable, "-m", "unittest", "-v", "tests/test_platform_skills_integrity.py"],
            "cwd": REPO_ROOT,
        },
    ]

    all_passed = True
    summary = []

    for step in test_steps:
        print(f"--- Running: {step['name']} ---")
        code, out, duration = run_command(step["cmd"], step["cwd"])
        status = "PASSED" if code == 0 else "FAILED"
        summary.append((step["name"], status, f"{duration:.3f}s"))

        print(out)
        if code != 0:
            all_passed = False
            print(f"❌ {step['name']} FAILED with exit code {code}\n")
        else:
            print(f"✅ {step['name']} PASSED in {duration:.3f}s\n")

    print("=======================================================================")
    print("📊 Verification Suite Summary")
    print("=======================================================================")
    for name, status, dur in summary:
        icon = "✅" if status == "PASSED" else "❌"
        print(f"{icon} {name:<45} [{status}] ({dur})")
    print("=======================================================================")

    if not all_passed:
        print("\n❌ Verification suite failed.")
        sys.exit(1)
    else:
        print("\n🎉 All tests passed cleanly without cluster deployment!")
        sys.exit(0)


if __name__ == "__main__":
    main()
