#!/usr/bin/env bash
# styleai-installer.sh
# Install / uninstall helper for StyleAI Lightroom plugin + backend server.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PLUGIN_SRC_ZIP="${PROJECT_ROOT}/build/StyleAI-Plugin-v1.0.0.zip"
PLUGIN_NAME="StyleAI.lrdevplugin"
PLUGIN_DEST_DIR="${HOME}/Library/Application Support/Adobe/Lightroom/Modules"
PLUGIN_DEST="${PLUGIN_DEST_DIR}/${PLUGIN_NAME}"
SERVER_PORT="19819"
SERVER_DIR="${PROJECT_ROOT}/server"

echo "========================================"
echo "  StyleAI Installer"
echo "========================================"
echo ""

# ── Helper functions ────────────────────────────────────────────────────────

port_in_use() {
    lsof -ti:"$SERVER_PORT" >/dev/null 2>&1
}

find_server_pid() {
    lsof -ti:"$SERVER_PORT" 2>/dev/null || true
}

print_usage() {
    echo "Usage:"
    echo "  $(basename "$0") install   — Install plugin to Lightroom"
    echo "  $(basename "$0") uninstall — Remove plugin from Lightroom"
    echo "  $(basename "$0") status    — Check installation & server status"
    echo "  $(basename "$0") server    — Start the backend server"
    echo ""
}

# ── Install ─────────────────────────────────────────────────────────────────

cmd_install() {
    echo "=== Installing StyleAI Plugin ==="
    echo ""

    # 1. Check for old LrGeniusAI conflict
    if [ -d "${HOME}/Library/Application Support/Adobe/Lightroom/Modules/LrGeniusAI.lrplugin" ]; then
        echo "WARNING: Old LrGeniusAI plugin detected."
        echo "Run: bash ${SCRIPT_DIR}/uninstall-lrgeniusai.sh"
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
        echo "This is likely the old LrGeniusAI server."
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

# ── Uninstall ───────────────────────────────────────────────────────────────

cmd_uninstall() {
    echo "=== Uninstalling StyleAI ==="
    echo ""

    # 1. Stop server if running from our source
    if port_in_use; then
        PID=$(find_server_pid)
        # Only kill if it's our uv/python process (not a system-wide install)
        if ps -p "$PID" -o command= 2>/dev/null | grep -q "styleai_server.py"; then
            echo "Stopping StyleAI server (PID $PID)..."
            kill "$PID" 2>/dev/null || true
            sleep 1
            if port_in_use; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            echo "Server stopped."
        else
            echo "Another server is running on port $SERVER_PORT (PID $PID)."
            echo "Not killing — may be the old LrGeniusAI server."
        fi
    fi

    # 2. Remove plugin
    if [ -d "$PLUGIN_DEST" ]; then
        echo "Removing Lightroom plugin..."
        rm -rf "$PLUGIN_DEST"
    else
        echo "Lightroom plugin not found."
    fi

    # 3. Remove user data (optional)
    echo ""
    read -r -p "Also remove user data (logs, training DB)? [y/N] " yn
    case "$yn" in
        [Yy]*)
            rm -rf "${HOME}/Library/Logs/StyleAI"
            rm -rf "${HOME}/Library/Application Support/StyleAI"
            echo "User data removed."
            ;;
        *)
            echo "User data preserved."
            ;;
    esac

    echo ""
    echo "StyleAI has been uninstalled."
    echo "Restart Lightroom Classic to complete removal."
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

    # Port conflict check
    if port_in_use; then
        PID=$(find_server_pid)
        if ! ps -p "$PID" -o command= 2>/dev/null | grep -q "styleai_server.py"; then
            echo ""
            echo "  WARNING: Port $SERVER_PORT is in use by a non-StyleAI process!"
            echo "  This may be the old LrGeniusAI server."
        fi
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
    uninstall|remove|rm|u)
        cmd_uninstall
        ;;
    status|s)
        cmd_status
        ;;
    server|start)
        cmd_server
        ;;
    *)
        echo "Unknown command: $COMMAND"
        print_usage
        exit 1
        ;;
esac
