#!/usr/bin/env bash
# server.sh
# Server management script for StyleAI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVER_PORT="19819"
SERVER_DIR="${PROJECT_ROOT}/server"

# ── Helper functions ────────────────────────────────────────────────────────

port_in_use() {
    lsof -sTCP:LISTEN -ti:"$SERVER_PORT" >/dev/null 2>&1
}

find_server_pid() {
    lsof -sTCP:LISTEN -ti:"$SERVER_PORT" 2>/dev/null || true
}

print_usage() {
    echo "Usage:"
    echo "  $(basename "$0") start     — Start the backend server in the foreground"
    echo "  $(basename "$0") stop      — Stop the backend server"
    echo "  $(basename "$0") status    — Check the backend server status"
    echo "  $(basename "$0") reset-db  — Reset StyleAI databases (requires server stop)"
    echo ""
}

# ── Server Operations ───────────────────────────────────────────────────────

cmd_start() {
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

cmd_stop() {
    echo "=== Stopping StyleAI Backend Server ==="
    echo ""
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
}

cmd_status() {
    echo "=== StyleAI Server Status ==="
    echo ""

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

cmd_reset_db() {
    echo "=== Resetting StyleAI Databases ==="
    echo ""
    if port_in_use; then
        PID=$(find_server_pid)
        echo "Stopping server (PID $PID) before resetting DB..."
        kill "$PID" 2>/dev/null || true
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
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    status|s)
        cmd_status
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
