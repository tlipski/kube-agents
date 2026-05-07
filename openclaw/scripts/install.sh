#!/usr/bin/env bash
set -e

# --- Configuration ---
LOCAL_BIN="$HOME/.local/bin"
REPO="${REPO:-https://github.com/gke-labs/kube-agents}"
BRANCH="${BRANCH:-main}"
TARBALL_URL="${TARBALL_URL:-${REPO}/archive/refs/heads/${BRANCH}.tar.gz}"
REQUIRED_OPENCLAW_VERSION="2026.5.3"
LOCAL_DIR="${LOCAL_DIR:-}"

# --- Pre-flight Checks ---
if ! command -v openclaw >/dev/null 2>&1; then
  echo "Error: 'openclaw' CLI is required. Please install OpenClaw first." >&2
  exit 1
fi

# Check OpenClaw version
OPENCLAW_VERSION=$(openclaw --version 2>/dev/null | awk '{print $3}')
if [ -n "$OPENCLAW_VERSION" ]; then
  # Simple version comparison using sort -V
  if [ "$(printf '%s\n' "$REQUIRED_OPENCLAW_VERSION" "$OPENCLAW_VERSION" | sort -V | head -n1)" != "$REQUIRED_OPENCLAW_VERSION" ]; then
    echo "========================================================================"
    echo " WARNING: Installed OpenClaw version ($OPENCLAW_VERSION) is older than $REQUIRED_OPENCLAW_VERSION."
    echo "          Some features (like automated heartbeat cronjobs) may not be"
    echo "          fully supported."
    echo "          It is highly recommended to upgrade OpenClaw to $REQUIRED_OPENCLAW_VERSION or"
    echo "          newer and re-run this script."
    echo "========================================================================"
    echo ""
  fi
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: 'curl' is required but not installed." >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: 'jq' is required but not installed." >&2
  exit 1
fi

SKIP_MCP=0
echo "[kube-agent] Verifying Google Cloud SDK (gcloud) setup..."
if ! gcloud container clusters list >/dev/null 2>&1; then
  echo "Warning: 'gcloud container clusters list' failed." >&2
  echo "         Please ensure 'gcloud' is installed and you are authenticated to a GCP project." >&2
  echo "         Skipping gke-mcp binary installation and MCP server registration." >&2
  SKIP_MCP=1
fi

# TODO: Enable MCP installation in a future update.
SKIP_MCP=1
echo ""
echo "========================================================================"
echo " NOTICE: Live GKE MCP server integration is an upcoming feature!"
echo "         Agents and skills are being installed now."
echo "         Real-time cluster operations via the gke-mcp server will be"
echo "         enabled in a future release. In the meantime, agents can still"
echo "         perform operations using standard gcloud and kubectl commands."
echo "========================================================================"
echo ""

if [ -n "${LOCAL_DIR}" ]; then
  # Resolve relative path to absolute
  LOCAL_DIR_ABS=$(cd "${LOCAL_DIR}" && pwd)
  if [ ! -d "${LOCAL_DIR_ABS}/openclaw/agents" ]; then
    echo "Error: LOCAL_DIR is set but ${LOCAL_DIR_ABS}/openclaw/agents does not exist." >&2
    exit 1
  fi
  echo "[kube-agent] Using local assets from ${LOCAL_DIR_ABS}..."
  SRC_AGENTS_DIR="${LOCAL_DIR_ABS}/openclaw/agents"
else
  TMP_DIR=$(mktemp -d)
  trap 'rm -rf "$TMP_DIR"' EXIT
  REPO_TARBALL="$TMP_DIR/repo.tar.gz"
  REPO_BASE_NAME=$(basename "$REPO")
  REPO_NAME="${REPO_BASE_NAME}-${BRANCH}"

  # Download repo tarball
  echo "[kube-agent] Downloading repository assets..."
  if ! curl -sSL "$TARBALL_URL" -o "$REPO_TARBALL"; then
    echo "Error: Failed to download repository tarball from $TARBALL_URL." >&2
    rm -rf "$TMP_DIR"
    exit 1
  fi

  # Extract ONLY agents (contains bundled skills)
  echo "[kube-agent] Extracting assets..."
  mkdir -p "$TMP_DIR/agents"
  tar -xzf "$REPO_TARBALL" -C "$TMP_DIR/agents" "$REPO_NAME/openclaw/agents" --strip-components=3 2>/dev/null || true
  
  SRC_AGENTS_DIR="$TMP_DIR/agents"
fi

# --- Phase 1: Install gke-mcp Binary ---
if [ "$SKIP_MCP" -eq 0 ]; then
  echo "--- Phase 1: Installing gke-mcp ---"

  mkdir -p "$LOCAL_BIN"

  if [ -f "$LOCAL_BIN/gke-mcp" ]; then
    echo "[kube-agent] gke-mcp is already installed at $LOCAL_BIN/gke-mcp"
  else
    echo "[kube-agent] Installing gke-mcp binary from official repository..."
    if ! curl -sSL "https://raw.githubusercontent.com/GoogleCloudPlatform/gke-mcp/main/install.sh" | \
         sed "s|/usr/local/bin|$LOCAL_BIN|g" | \
         sed 's/|| sudo install .*//g' | \
         sed 's/curl -fSL/curl -s -fSL/g' | \
         bash; then
      echo "Error: Execution of gke-mcp install script failed." >&2
      [ -n "${TMP_DIR:-}" ] && rm -rf "$TMP_DIR"
      exit 1
    fi
    echo "✅ gke-mcp binary installation complete."
  fi
else
  echo "--- Phase 1: Skipped (Upcoming Feature) ---"
  echo "    The 'gke-mcp' binary provides the core capability for agents to read"
  echo "    cluster states, inspect resources, and view logs. This functionality"
  echo "    will be unavailable in this release."
fi

# --- Phase 2: Register Agents in OpenClaw ---
echo "--- Phase 2: Registering OpenClaw Agents ---"

AGENTS=()

# Discover agents
if [ -d "$SRC_AGENTS_DIR" ]; then
  for AGENT_DIR in "$SRC_AGENTS_DIR"/*; do
    [ -e "$AGENT_DIR" ] || continue
    if [ -d "$AGENT_DIR" ]; then
      AGENT_NAME=$(basename "$AGENT_DIR")
      AGENTS+=("$AGENT_NAME")
      WORKSPACE_DIR="$HOME/.openclaw/workspace/agents/$AGENT_NAME"
      
      echo "Processing agent: $AGENT_NAME"

      if openclaw agents list | grep -q "^- $AGENT_NAME$"; then
        echo "[kube-agent] Agent '$AGENT_NAME' is already registered in OpenClaw."
      else
        echo "[kube-agent] Adding agent '$AGENT_NAME' to OpenClaw..."
        if ! openclaw agents add "$AGENT_NAME" --workspace "$WORKSPACE_DIR" --non-interactive; then
           echo "Error: Failed to add agent using OpenClaw CLI." >&2
           exit 1
        fi
      fi

      echo "[kube-agent] Copying agent assets to workspace ($WORKSPACE_DIR)..."
      mkdir -p "$WORKSPACE_DIR"
      
      # Copy all agent files from temp extraction
      cp -a "$AGENT_DIR/." "$WORKSPACE_DIR/"


      # Identity setup assumes files are present in the workspace
      if [ -f "$WORKSPACE_DIR/IDENTITY.md" ]; then
        echo "[kube-agent] Applying identity from IDENTITY.md for $AGENT_NAME..."
        if ! openclaw agents set-identity --agent "$AGENT_NAME" --workspace "$WORKSPACE_DIR" --from-identity; then
           echo "Warning: Failed to set identity for $AGENT_NAME." >&2
        fi
      fi
    fi
  done
else
  echo "Warning: No agents directory found in tarball."
fi

# --- Phase 3: Register MCP Server ---
if [ "$SKIP_MCP" -eq 0 ]; then
  echo "--- Phase 3: Registering MCP Server (gke-mcp) ---"
  if openclaw mcp list | grep -q "^- gke-mcp$"; then
    echo "[kube-agent] MCP server 'gke-mcp' is already registered."
  else
    echo "[kube-agent] Adding MCP server 'gke-mcp'..."
    # Use JSON string for the server configuration
    MCP_CONFIG="{\"command\":\"$LOCAL_BIN/gke-mcp\",\"args\":[],\"env\":{}}"
    if ! openclaw mcp set gke-mcp "$MCP_CONFIG"; then
      echo "Error: Failed to register MCP server." >&2
    fi
  fi
else
  echo "--- Phase 3: Skipped (Upcoming Feature) ---"
  echo "    The OpenClaw gateway will not bridge the GKE MCP tools to your agents yet."
fi

# --- Phase 4: Configure Semantic Routing ---
echo "--- Phase 4: Configuring Semantic Routing ---"
if [ ${#AGENTS[@]} -gt 0 ]; then
  # Get the current allowAgents array (defaulting to empty array if not set)
  CURRENT_ALLOW_AGENTS=$(openclaw config get agents.defaults.subagents.allowAgents 2>/dev/null || echo "[]")

  # Use jq to add all agents to the array
  AGENTS_JSON_ARRAY=$(printf '%s\n' "${AGENTS[@]}" | jq -R . | jq -s -c .)
  NEW_ALLOW_AGENTS=$(echo "$CURRENT_ALLOW_AGENTS" | jq -c ". + $AGENTS_JSON_ARRAY | unique")

  # Patch the configuration with the updated array
  echo "{\"agents\":{\"defaults\":{\"subagents\":{\"allowAgents\":$NEW_ALLOW_AGENTS}}}}" | openclaw config patch --stdin
else
  echo "No agents to configure for semantic routing."
fi

# --- Phase 5: Configure Heartbeat Cronjobs ---
echo "--- Phase 5: Configuring Heartbeat Cronjobs ---"
if [ ${#AGENTS[@]} -gt 0 ]; then
  CURRENT_AGENTS_LIST=$(openclaw config get agents.list 2>/dev/null || echo "[]")
  
  # For each agent we just installed, update its heartbeat in the list
  UPDATED_AGENTS_LIST="$CURRENT_AGENTS_LIST"
  for AGENT_NAME in "${AGENTS[@]}"; do
    UPDATED_AGENTS_LIST=$(echo "$UPDATED_AGENTS_LIST" | jq "map(if .id == \"$AGENT_NAME\" then .heartbeat = {\"every\": \"1m\", \"session\": \"agent:$AGENT_NAME:main\", \"target\": \"last\"} else . end)")
  done

  # Apply the updated agents list
  echo "{\"agents\":{\"list\":$UPDATED_AGENTS_LIST}}" | openclaw config patch --stdin
else
  echo "No agents to configure for heartbeats."
fi


# Cleanup
[ -n "${TMP_DIR:-}" ] && rm -rf "$TMP_DIR"

echo "--- Installation Complete ---"
if [ ${#AGENTS[@]} -gt 0 ]; then
  echo "You can now start the gateway and interact with your new GKE agents."
fi
