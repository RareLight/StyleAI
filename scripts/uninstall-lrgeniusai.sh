#!/usr/bin/env bash
# uninstall-lrgeniusai.sh
# Complete uninstaller for LrGeniusAI v2.x (original package).
# Removes everything installed by the .pkg installer.

set -euo pipefail

APP_NAME="LrGeniusAI"
LAUNCH_AGENT="com.lrgenius.server"
PORT="19819"

echo "========================================"
echo "  LrGeniusAI Uninstaller"
echo "========================================"
echo ""

# ── 1. Detect old process ───────────────────────────────────────────────────
OLD_PID=$(lsof -ti:"$PORT" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    echo "Found process on port $PORT (PID $OLD_PID) — stopping..."
    sudo kill -9 "$OLD_PID" 2>/dev/null || true
fi

# ── 2. Unload & remove LaunchAgent ──────────────────────────────────────────
if [ -f "/Library/LaunchAgents/${LAUNCH_AGENT}.plist" ]; then
    echo "Removing LaunchAgent ${LAUNCH_AGENT}..."
    sudo launchctl unload "/Library/LaunchAgents/${LAUNCH_AGENT}.plist" 2>/dev/null || true
    sudo launchctl remove "$LAUNCH_AGENT" 2>/dev/null || true
    sudo rm -f "/Library/LaunchAgents/${LAUNCH_AGENT}.plist"
else
    echo "LaunchAgent not found (already removed?)"
fi

# ── 3. Kill any stray server processes ────────────────────────────────────
echo "Killing any stray backend processes..."
sudo pkill -f "geniusai_server.py" 2>/dev/null || true
sudo pkill -f "lrgenius-server" 2>/dev/null || true

# ── 4. Remove application bundle ──────────────────────────────────────────
if [ -d "/Applications/${APP_NAME}" ]; then
    echo "Removing /Applications/${APP_NAME}..."
    sudo rm -rf "/Applications/${APP_NAME}"
else
    echo "Application bundle not found (already removed?)"
fi

# ── 5. Remove system-wide logs ────────────────────────────────────────────
if [ -d "/Library/Logs/${APP_NAME}" ]; then
    echo "Removing /Library/Logs/${APP_NAME}..."
    sudo rm -rf "/Library/Logs/${APP_NAME}"
fi

# ── 6. Remove Lightroom plugin ────────────────────────────────────────────
PLUGIN_DIR="${HOME}/Library/Application Support/Adobe/Lightroom/Modules/${APP_NAME}.lrplugin"
if [ -d "$PLUGIN_DIR" ]; then
    echo "Removing Lightroom plugin..."
    rm -rf "$PLUGIN_DIR"
else
    echo "Lightroom plugin not found (already removed?)"
fi

# ── 7. Remove user logs & support data ────────────────────────────────────
if [ -d "${HOME}/Library/Logs/${APP_NAME}" ]; then
    echo "Removing user logs..."
    rm -rf "${HOME}/Library/Logs/${APP_NAME}"
fi

if [ -d "${HOME}/Library/Application Support/${APP_NAME}" ]; then
    echo "Removing user support data..."
    rm -rf "${HOME}/Library/Application Support/${APP_NAME}"
fi

# ── 8. Remove quarantine attributes from Downloads (cleanup) ────────────────
if command -v xattr >/dev/null 2>&1; then
    echo "Clearing quarantine attributes..."
    xattr -dr com.apple.quarantine "${HOME}/Downloads" 2>/dev/null || true
fi

# ── 9. Verification ───────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Verification"
echo "========================================"

# Port check
if lsof -ti:"$PORT" >/dev/null 2>&1; then
    echo "  WARNING: Port $PORT is still in use!"
    lsof -ti:"$PORT" | xargs ps -p 2>/dev/null || true
else
    echo "  Port $PORT is free ✓"
fi

# LaunchAgent check
if launchctl list 2>/dev/null | grep -q "$LAUNCH_AGENT"; then
    echo "  WARNING: LaunchAgent $LAUNCH_AGENT still registered"
else
    echo "  LaunchAgent removed ✓"
fi

# App check
if [ -d "/Applications/${APP_NAME}" ]; then
    echo "  WARNING: /Applications/${APP_NAME} still exists"
else
    echo "  Application bundle removed ✓"
fi

# Plugin check
if [ -d "$PLUGIN_DIR" ]; then
    echo "  WARNING: Lightroom plugin still exists"
else
    echo "  Lightroom plugin removed ✓"
fi

echo ""
echo "========================================"
echo "  ${APP_NAME} has been removed."
echo "========================================"
