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

is_styleai_process() {
    local pid="$1"
    local command
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    [[ "$command" == *"styleai_server.py"* || "$command" == *"styleai-server"* ]]
}

post_control_request() {
    local endpoint="$1"
    curl --silent --show-error --fail --max-time 2 \
        --request POST "http://127.0.0.1:${SERVER_PORT}${endpoint}" >/dev/null
}

wait_for_port_release() {
    local timeout_seconds="$1"
    local elapsed=0
    while port_in_use; do
        if (( elapsed >= timeout_seconds )); then
            return 1
        fi
        sleep 1
        ((elapsed += 1))
    done
    return 0
}

print_usage() {
    echo "Usage:"
    echo "  $(basename "$0") start     — Start the backend server in the foreground"
    echo "  $(basename "$0") stop      — Stop the backend server"
    echo "  $(basename "$0") status    — Check the backend server status"
    echo "  $(basename "$0") reset-db <catalog-path>  — Reset one catalog's StyleAI database"
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
    local pids
    pids=$(find_server_pid)
    if [ -z "$pids" ]; then
        echo "No running server detected on port $SERVER_PORT."
        return 0
    fi

    local pid
    for pid in $pids; do
        if ! is_styleai_process "$pid"; then
            echo "ERROR: PID $pid owns port $SERVER_PORT but is not a recognized StyleAI backend."
            echo "Refusing to terminate an unrelated process."
            return 1
        fi
    done

    echo "Requesting cancellation and graceful shutdown for StyleAI (PID(s): $pids)..."
    post_control_request "/cancel_all_tasks" || \
        echo "WARNING: Backend did not acknowledge task cancellation; continuing with shutdown."
    post_control_request "/shutdown" || \
        echo "WARNING: Backend did not acknowledge graceful shutdown; using process termination if needed."

    if wait_for_port_release 6; then
        echo "Server stopped successfully."
        return 0
    fi

    echo "Server did not stop within 6 seconds; sending SIGTERM..."
    for pid in $pids; do
        kill "$pid" 2>/dev/null || true
    done
    if wait_for_port_release 3; then
        echo "Server stopped successfully."
        return 0
    fi

    echo "Server did not exit after SIGTERM; sending SIGKILL..."
    for pid in $pids; do
        kill -9 "$pid" 2>/dev/null || true
    done
    if ! wait_for_port_release 2; then
        echo "ERROR: Port $SERVER_PORT remains in use after forced shutdown."
        return 1
    fi
    echo "Server stopped successfully."
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
    local catalog_path="${1:-}"
    if [ -z "$catalog_path" ] || [ ! -f "$catalog_path" ]; then
        echo "ERROR: Provide the path to the one Lightroom catalog whose database should be reset."
        echo "Example: $(basename "$0") reset-db \"$HOME/Pictures/Lightroom/My Catalog.lrcat\""
        return 1
    fi
    cmd_stop
    local db_path
    db_path="$(dirname "$catalog_path")/styleai.db"
    if [ ! -d "$db_path" ]; then
        echo "No StyleAI database exists for: $catalog_path"
        return 0
    fi
    rm -rf "$db_path"
    echo "Deleted catalog-local StyleAI database: $db_path"
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
        cmd_reset_db "$@"
        ;;
    *)
        echo "Unknown command: $COMMAND"
        print_usage
        exit 1
        ;;
esac
