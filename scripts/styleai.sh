#!/usr/bin/env bash
# styleai.sh
# Complete uninstaller for StyleAI and legacy LrGeniusAI.
# Removes everything installed by the installers or build processes.

set -euo pipefail

PORT="19819"

echo "========================================"
echo "  StyleAI Uninstaller"
echo "========================================"
echo ""

# ── 1. Stop active processes on port 19819 ─────────────────────────────────────
OLD_PID=$(lsof -sTCP:LISTEN -ti:"$PORT" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    echo "Found process on port $PORT (PID $OLD_PID) — stopping..."
    sudo kill "$OLD_PID" 2>/dev/null || true
    sleep 1
    # Check if still running, escalate to SIGKILL
    if lsof -sTCP:LISTEN -ti:"$PORT" >/dev/null 2>&1; then
        echo "Process did not exit gracefully; sending SIGKILL..."
        sudo kill -9 "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    echo "Process stopped."
else
    echo "No processes found running on port $PORT."
fi
echo ""

# ── 2. Unload & remove LaunchAgents ─────────────────────────────────────────
for LAUNCH_AGENT in "com.styleai.server" "com.lrgenius.server"; do
    if [ -f "/Library/LaunchAgents/${LAUNCH_AGENT}.plist" ]; then
        echo "Removing system-wide LaunchAgent ${LAUNCH_AGENT}..."
        # Try unloading for current GUI user if console is attached
        CURRENT_USER=$(stat -f '%u' /dev/console 2>/dev/null || echo "")
        if [ -n "$CURRENT_USER" ] && [ "$CURRENT_USER" -ne 0 ]; then
            sudo launchctl asuser "$CURRENT_USER" launchctl unload "/Library/LaunchAgents/${LAUNCH_AGENT}.plist" 2>/dev/null || true
        fi
        sudo launchctl unload "/Library/LaunchAgents/${LAUNCH_AGENT}.plist" 2>/dev/null || true
        sudo rm -f "/Library/LaunchAgents/${LAUNCH_AGENT}.plist"
        echo "Removed ${LAUNCH_AGENT}.plist"
    fi

    # Also check user-specific LaunchAgents
    if [ -f "${HOME}/Library/LaunchAgents/${LAUNCH_AGENT}.plist" ]; then
        echo "Removing user-specific LaunchAgent ${LAUNCH_AGENT}..."
        launchctl unload "${HOME}/Library/LaunchAgents/${LAUNCH_AGENT}.plist" 2>/dev/null || true
        rm -f "${HOME}/Library/LaunchAgents/${LAUNCH_AGENT}.plist"
        echo "Removed user-specific ${LAUNCH_AGENT}.plist"
    fi
done
echo ""

# ── 3. Kill any stray server processes ────────────────────────────────────
echo "Killing any stray backend processes..."
sudo pkill -f "styleai_server.py" 2>/dev/null || true
sudo pkill -f "styleai-server" 2>/dev/null || true
sudo pkill -f "geniusai_server.py" 2>/dev/null || true
sudo pkill -f "lrgenius-server" 2>/dev/null || true
echo "Cleanup of stray processes complete."
echo ""

# ── 4. Remove application bundles ──────────────────────────────────────────
for APP in "StyleAI" "LrGeniusAI"; do
    if [ -d "/Applications/${APP}" ]; then
        echo "Removing /Applications/${APP}..."
        sudo rm -rf "/Applications/${APP}"
    fi
done
echo ""

# ── 5. Remove system-wide logs ────────────────────────────────────────────
for APP in "StyleAI" "LrGeniusAI"; do
    if [ -d "/Library/Logs/${APP}" ]; then
        echo "Removing system-wide logs /Library/Logs/${APP}..."
        sudo rm -rf "/Library/Logs/${APP}"
    fi
done
echo ""

# ── 6. Remove Lightroom plugins ────────────────────────────────────────────
echo "Removing Lightroom plugins..."
PLUGINS=(
    "StyleAI.lrplugin"
    "StyleAI.lrdevplugin"
    "LrGeniusAI.lrplugin"
)
for PLUGIN in "${PLUGINS[@]}"; do
    PLUGIN_PATH="${HOME}/Library/Application Support/Adobe/Lightroom/Modules/${PLUGIN}"
    if [ -d "$PLUGIN_PATH" ]; then
        echo "Removing: $PLUGIN_PATH"
        rm -rf "$PLUGIN_PATH"
    fi
done
echo ""

# ── 7. Remove user logs & support data ────────────────────────────────────
echo "Removing user logs and support data..."
for APP in "StyleAI" "LrGeniusAI"; do
    if [ -d "${HOME}/Library/Logs/${APP}" ]; then
        echo "Removing user logs at ${HOME}/Library/Logs/${APP}..."
        rm -rf "${HOME}/Library/Logs/${APP}"
    fi
done

# We will prompt before deleting user databases/support data to avoid accidental loss
echo ""
read -r -p "Do you want to delete user databases & settings (e.g. face database, configuration)? [y/N] " yn
case "$yn" in
    [Yy]*)
        for APP in "StyleAI" "LrGeniusAI"; do
            SUPPORT_DIR="${HOME}/Library/Application Support/${APP}"
            if [ -d "$SUPPORT_DIR" ]; then
                echo "Removing: $SUPPORT_DIR"
                rm -rf "$SUPPORT_DIR"
            fi
        done
        echo "User settings and databases removed."
        ;;
    *)
        echo "User settings and databases preserved."
        ;;
esac
echo ""

# ── 8. Remove quarantine attributes from Downloads (cleanup) ────────────────
if command -v xattr >/dev/null 2>&1; then
    echo "Clearing quarantine attributes..."
    xattr -dr com.apple.quarantine "${HOME}/Downloads" 2>/dev/null || true
fi
echo ""

# ── 9. Verification ───────────────────────────────────────────────────────
echo "========================================"
echo "  Verification"
echo "========================================"

# Port check
if lsof -sTCP:LISTEN -ti:"$PORT" >/dev/null 2>&1; then
    echo "  WARNING: Port $PORT is still in use!"
    lsof -sTCP:LISTEN -ti:"$PORT" | xargs ps -p 2>/dev/null || true
else
    echo "  Port $PORT is free ✓"
fi

# LaunchAgent check
if launchctl list 2>/dev/null | grep -E "com.styleai.server|com.lrgenius.server" >/dev/null; then
    echo "  WARNING: StyleAI/LrGeniusAI LaunchAgent is still registered"
else
    echo "  LaunchAgents removed ✓"
fi

# App check
if [ -d "/Applications/StyleAI" ] || [ -d "/Applications/LrGeniusAI" ]; then
    echo "  WARNING: Application bundle still exists under /Applications"
else
    echo "  Application bundles removed ✓"
fi

# Plugin check
PLUGIN_REMAINING=false
for PLUGIN in "${PLUGINS[@]}"; do
    if [ -d "${HOME}/Library/Application Support/Adobe/Lightroom/Modules/${PLUGIN}" ]; then
        PLUGIN_REMAINING=true
    fi
done

if [ "$PLUGIN_REMAINING" = true ]; then
    echo "  WARNING: Lightroom plugin folders still exist in Lightroom Modules directory"
else
    echo "  Lightroom plugins removed ✓"
fi

echo ""
echo "========================================"
echo "  Uninstallation complete."
echo "========================================"
