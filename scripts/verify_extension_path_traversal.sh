#!/usr/bin/env bash
set -euo pipefail

# Verify AgentPlugin filePath directory traversal validation in k8s-operator
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== Running k8s-operator extension file path validation unit tests ==="
cd "${REPO_ROOT}/k8s-operator"
go test -v ./internal/controller -run "TestIsValidExtensionFilePath|TestExtractExtensionPlatformNames_PathTraversal|TestHasExtensionFiles_PathTraversal|TestBuildExtensionsConfigMap_PathTraversal"

echo "=== Running all controller unit tests ==="
go test ./internal/controller/...

echo "Verification complete!"
