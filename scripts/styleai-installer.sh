#!/usr/bin/env bash
# styleai-installer.sh
# Unified installer, manager, and redeployment tool for StyleAI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="StyleAI.lrdevplugin"
PLUGIN_SRC_ZIP="${PROJECT_ROOT}/build/StyleAI-Plugin-v1.0.0.zip"
PLUGIN_SRC_DIR="${PROJECT_ROOT}/plugin/${PLUGIN_NAME}"
PLUGIN_DEST_DIR="${HOME}/Library/Application Support/Adobe/Lightroom/Modules"
PLUGIN_DEST="${PLUGIN_DEST_DIR}/${PLUGIN_NAME}"
SERVER_PORT="19819"
SERVER_DIR="${PROJECT_ROOT}/server"
LOG_DIR="${HOME}/Library/Logs/StyleAI"
LAUNCH_LOG="${LOG_DIR}/styleai-server-launcher.log"

echo "========================================"
echo "  StyleAI Installer & Manager"
echo "========================================"
echo ""

# ── Helper functions ────────────────────────────────────────────────────────

port_in_use() {
    lsof -sTCP:LISTEN -ti:"$SERVER_PORT" >/dev/null 2>&1
}

find_server_pid() {
    lsof -sTCP:LISTEN -ti:"$SERVER_PORT" 2>/dev/null || true
}

print_usage() {
    echo "Usage:"
    echo "  $(basename "$0") install   — Install plugin to Lightroom"
    echo "  $(basename "$0") redeploy  — Stop server, redeploy dev plugin, and restart server in background"
    echo "  $(basename "$0") status    — Check installation & server status"
    echo "  $(basename "$0") server    — Start the backend server in foreground"
    echo ""
}

# ── Install ─────────────────────────────────────────────────────────────────

cmd_install() {
    echo "=== Installing StyleAI Plugin ==="
    echo ""

    # 1. Check for old LrGeniusAI conflict
    if [ -d "${HOME}/Library/Application Support/Adobe/Lightroom/Modules/LrGeniusAI.lrplugin" ]; then
        echo "WARNING: Old LrGeniusAI plugin detected."
        echo "Please run: ./scripts/styleai.sh"
        echo ""
        read -r -p "Continue anyway? [y/N] " yn
        case "$yn" in
            [Yy]*) ;;
            *) echo "Aborting."; exit 1 ;;
        esac
    fi

    # 2. Check for old server on port 19819
    if port_in_use; then
        OLD_PID=$(find_server_pid)
        echo "WARNING: Port $SERVER_PORT is already in use (PID $OLD_PID)."
        echo "This is likely the running StyleAI or legacy server."
        echo ""
        read -r -p "Kill it and continue? [y/N] " yn
        case "$yn" in
            [Yy]*) kill -9 "$OLD_PID" 2>/dev/null || true; sleep 1 ;;
            *) echo "Aborting."; exit 1 ;;
        esac
    fi

    # 3. Build plugin zip if missing
    if [ ! -f "$PLUGIN_SRC_ZIP" ]; then
        echo "Plugin zip not found. Building..."
        if [ -f "${SCRIPT_DIR}/package-plugin.sh" ]; then
            bash "${SCRIPT_DIR}/package-plugin.sh"
        else
            echo "ERROR: Package script not found at ${SCRIPT_DIR}/package-plugin.sh"
            exit 1
        fi
    fi

    # 4. Unzip and install
    echo "Installing plugin to Lightroom Modules..."
    mkdir -p "$PLUGIN_DEST_DIR"

    # Remove old version if present
    if [ -d "$PLUGIN_DEST" ]; then
        echo "Removing previous StyleAI plugin..."
        rm -rf "$PLUGIN_DEST"
    fi

    # Extract
    TMP_DIR=$(mktemp -d)
    unzip -q "$PLUGIN_SRC_ZIP" -d "$TMP_DIR"
    cp -a "${TMP_DIR}/${PLUGIN_NAME}" "$PLUGIN_DEST"
    rm -rf "$TMP_DIR"

    echo "Plugin installed ✓"
    echo "  Location: $PLUGIN_DEST"
    echo ""

    # 5. Verify
    if [ -d "$PLUGIN_DEST" ]; then
        echo "========================================"
        echo "  Installation successful!"
        echo "========================================"
        echo ""
        echo "Next steps:"
        echo "  1. Restart Lightroom Classic"
        echo "  2. Start the backend: $(basename "$0") server"
        echo "  3. In Lightroom: File → Plug-in Manager → StyleAI"
    else
        echo "ERROR: Plugin installation failed."
        exit 1
    fi
}

# ── Redeploy ────────────────────────────────────────────────────────────────

cmd_redeploy() {
    echo "=== Redeploying StyleAI (Local Dev Mode) ==="
    echo ""

    # 1. Stop backend server if running
    PID=$(find_server_pid)
    if [ -n "$PID" ]; then
        echo "Stopping backend server on port $SERVER_PORT (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        
        # Wait up to 5 seconds for it to exit
        for i in {1..5}; do
            if ! port_in_use; then
                break
            fi
            sleep 1
        done
        
        # If still running, force kill
        if port_in_use; then
            echo "Server did not exit gracefully; sending SIGKILL..."
            kill -9 "$PID" 2>/dev/null || true
            sleep 1
        fi
        echo "Server stopped successfully."
    else
        echo "No running server detected on port $SERVER_PORT."
    fi
    echo ""

    # 2. Clean up old installation files
    if [ -d "$PLUGIN_DEST" ]; then
        echo "Removing old installation files at:"
        echo "  $PLUGIN_DEST"
        rm -rf "$PLUGIN_DEST"
        echo "Cleanup complete."
    else
        echo "No previous installation directory found at destination."
    fi
    echo ""

    # 3. Install updated files directly from source
    echo "Installing updated plugin files directly from source..."
    mkdir -p "$PLUGIN_DEST_DIR"
    cp -R "$PLUGIN_SRC_DIR" "$PLUGIN_DEST"
    echo "Plugin successfully installed to:"
    echo "  $PLUGIN_DEST"
    echo ""

    # 4. Restart backend server in background
    mkdir -p "$LOG_DIR"
    echo "Restarting backend server in background..."
    if ! command -v uv >/dev/null 2>&1; then
        echo "ERROR: 'uv' not found in PATH."
        echo "Please make sure 'uv' is installed and available in your environment."
        exit 1
    fi

    cd "$SERVER_DIR"
    # Run in background via nohup
    nohup uv run python src/styleai_server.py > "$LAUNCH_LOG" 2>&1 &

    # Wait up to 5 seconds to verify it starts up on port 19819
    NEW_PID=""
    for i in {1..5}; do
        sleep 1
        NEW_PID=$(find_server_pid)
        if [ -n "$NEW_PID" ]; then
            break
        fi
    done

    if [ -n "$NEW_PID" ]; then
        echo "========================================"
        echo "  Redeployment successful!"
        echo "========================================"
        echo "Backend server is running (PID: $NEW_PID)"
        echo "Launcher log: $LAUNCH_LOG"
    else
        echo "WARNING: Server launched but not yet responding on port $SERVER_PORT."
        echo "Please check launcher logs: $LAUNCH_LOG"
    fi
    echo ""
}

# ── Status ──────────────────────────────────────────────────────────────────

cmd_status() {
    echo "=== StyleAI Status ==="
    echo ""

    # Plugin
    if [ -d "$PLUGIN_DEST" ]; then
        echo "  Plugin:     Installed ✓"
        echo "  Location:   $PLUGIN_DEST"
    else
        echo "  Plugin:     NOT installed"
    fi

    # Server
    if port_in_use; then
        PID=$(find_server_pid)
        CMD=$(ps -p "$PID" -o command= 2>/dev/null || echo "unknown")
        echo "  Server:     Running on port $SERVER_PORT (PID $PID)"
        echo "  Command:    $CMD"
    else
        echo "  Server:     Not running"
    fi

    echo ""
    echo "  Project root: $PROJECT_ROOT"
    echo "  Server dir:   $SERVER_DIR"
}

# ── Server ──────────────────────────────────────────────────────────────────

cmd_server() {
    echo "=== Starting StyleAI Backend Server ==="
    echo ""

    # Check uv
    if ! command -v uv >/dev/null 2>&1; then
        echo "ERROR: 'uv' not found in PATH."
        echo "Install it: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi

    # Check server source exists
    if [ ! -f "${SERVER_DIR}/src/styleai_server.py" ]; then
        echo "ERROR: Server source not found at ${SERVER_DIR}/src/styleai_server.py"
        exit 1
    fi

    # Check for port conflict
    if port_in_use; then
        PID=$(find_server_pid)
        echo "Port $SERVER_PORT is already in use (PID $PID)."
        if ps -p "$PID" -o command= 2>/dev/null | grep -q "styleai_server.py"; then
            echo "StyleAI server is already running."
            exit 0
        else
            echo "ERROR: Another process is using port $SERVER_PORT."
            exit 1
        fi
    fi

    # Start server
    echo "Starting server..."
    echo "  Press Ctrl+C to stop"
    echo ""
    cd "$SERVER_DIR"
    uv run python src/styleai_server.py
}

cmd_reset_db() {
    echo "Resetting StyleAI databases..."
    if port_in_use; then
        PID=$(find_server_pid)
        echo "Stopping server (PID $PID) before resetting DB..."
        kill "$PID"
        sleep 2
    fi
    find "${HOME}/Pictures" -type d -name "styleai.db" -prune -exec rm -rf {} +
    echo "All styleai.db directories in ~/Pictures have been deleted."
}

# ── Main ────────────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
    print_usage
    exit 0
fi

COMMAND="${1:-}"
shift || true

case "$COMMAND" in
    install|i)
        cmd_install
        ;;
    redeploy|r)
        cmd_redeploy
        ;;
    status|s)
        cmd_status
        ;;
    server|start)
        cmd_server
        ;;
    reset-db)
        cmd_reset_db
        ;;
    *)
        echo "Unknown command: $COMMAND"
        print_usage
        exit 1
        ;;
esac
