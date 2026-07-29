#!/usr/bin/env python3
import os

filepath = "/usr/local/google/home/tomeklipski/d/ka-dev/k8s-operator/internal/controller/platformagent_manifests.go"
with open(filepath) as f:
    content = f.read()

lines = content.splitlines()
for i, l in enumerate(lines):
    if "buildExtensionsConfigMap" in l or "buildExtensionInstallerContainer" in l or "mergeExtensionConfigs" in l:
        print(f"Line {i+1}: {l}")
        for j in range(i, min(i+35, len(lines))):
            print(f"  {j+1}: {lines[j]}")
        print("="*40)
