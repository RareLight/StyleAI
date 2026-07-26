#!/usr/bin/env bash
# styleai-installer.sh
# Unified installer, manager, and redeployment tool for StyleAI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="StyleAI.lrdevplugin"
PLUGIN_SRC_DIR="${PROJECT_ROOT}/plugin/${PLUGIN_NAME}"
PLUGIN_DEST_DIR="${HOME}/Library/Application Support/Adobe/Lightroom/Modules"
PLUGIN_DEST="${PLUGIN_DEST_DIR}/${PLUGIN_NAME}"

echo "========================================"
echo "  StyleAI Installer & Manager"
echo "========================================"
echo ""

# ── Helper functions ────────────────────────────────────────────────────────

ensure_lightroom_is_stopped() {
    if pgrep -f '[L]ightroom Classic' >/dev/null 2>&1; then
        echo "ERROR: Lightroom Classic is running."
        echo "Quit Lightroom completely before installing or redeploying StyleAI so it cannot retain old plugin code."
        return 1
    fi
}

verify_plugin_tree() {
    local candidate="$1"
    if [ ! -f "$candidate/Info.lua" ] || [ ! -f "$candidate/Init.lua" ]; then
        echo "ERROR: Plugin verification failed: required entry files are missing from $candidate"
        return 1
    fi
    if ! diff -qr "$PLUGIN_SRC_DIR" "$candidate" >/dev/null; then
        echo "ERROR: Plugin verification failed: deployed files differ from the source tree."
        return 1
    fi
}

deploy_plugin_tree() {
    if [ ! -d "$PLUGIN_SRC_DIR" ]; then
        echo "ERROR: Plugin source directory not found: $PLUGIN_SRC_DIR"
        return 1
    fi
    if [ ! -f "$PLUGIN_SRC_DIR/Info.lua" ] || [ ! -f "$PLUGIN_SRC_DIR/Init.lua" ]; then
        echo "ERROR: Plugin source tree is incomplete."
        return 1
    fi

    mkdir -p "$PLUGIN_DEST_DIR"
    local staging_dir backup_dir
    staging_dir=$(mktemp -d "${PLUGIN_DEST_DIR}/.${PLUGIN_NAME}.staging.XXXXXX")
    backup_dir="${PLUGIN_DEST_DIR}/.${PLUGIN_NAME}.previous.$$"

    cp -R "$PLUGIN_SRC_DIR" "$staging_dir/$PLUGIN_NAME"
    verify_plugin_tree "$staging_dir/$PLUGIN_NAME"

    if [ -e "$PLUGIN_DEST" ]; then
        mv "$PLUGIN_DEST" "$backup_dir"
    fi
    if ! mv "$staging_dir/$PLUGIN_NAME" "$PLUGIN_DEST"; then
        echo "ERROR: Could not activate the staged plugin deployment."
        if [ -e "$backup_dir" ]; then mv "$backup_dir" "$PLUGIN_DEST"; fi
        rm -rf "$staging_dir"
        return 1
    fi
    rmdir "$staging_dir"

    if ! verify_plugin_tree "$PLUGIN_DEST"; then
        echo "ERROR: New plugin deployment failed verification; restoring the previous plugin."
        rm -rf "$PLUGIN_DEST"
        if [ -e "$backup_dir" ]; then mv "$backup_dir" "$PLUGIN_DEST"; fi
        return 1
    fi
    if [ -e "$backup_dir" ]; then rm -rf "$backup_dir"; fi
}

print_usage() {
    echo "Usage:"
    echo "  $(basename "$0") install   — Install plugin to Lightroom"
    echo "  $(basename "$0") redeploy  — Stop backend and atomically replace the dev plugin (Lightroom must be closed)"
    echo ""
}

# ── Install ─────────────────────────────────────────────────────────────────

cmd_install() {
    echo "=== Installing StyleAI Plugin ==="
    echo ""

    ensure_lightroom_is_stopped

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

    # Install directly from the checked-out source so install cannot use a stale build archive.
    echo "Installing plugin from the current source tree..."
    bash "${SCRIPT_DIR}/server.sh" stop
    deploy_plugin_tree

    echo "Plugin installed ✓"
    echo "  Location: $PLUGIN_DEST"
    echo ""

    # Verify
    if [ -d "$PLUGIN_DEST" ]; then
        echo "========================================"
        echo "  Installation successful!"
        echo "========================================"
        echo ""
        echo "Next steps:"
        echo "  1. Start Lightroom Classic"
        echo "  2. Start Lightroom Classic; the plugin starts the catalog-local backend automatically."
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

    ensure_lightroom_is_stopped

    # 1. Stop backend server and verify that port 19819 is released.
    bash "${SCRIPT_DIR}/server.sh" stop
    echo ""

    # 2. Stage, verify, and atomically activate the complete source tree.
    echo "Staging and verifying updated plugin files from source..."
    deploy_plugin_tree
    echo "Plugin successfully installed to:"
    echo "  $PLUGIN_DEST"
    echo "Start Lightroom Classic to load the new plugin and launch the current backend source."
    echo ""
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
    *)
        echo "Unknown command: $COMMAND"
        print_usage
        exit 1
        ;;
esac
