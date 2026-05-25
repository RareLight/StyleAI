#!/usr/bin/env bash
# Package the StyleAI Lightroom plugin for distribution.
# Creates a zip file ready for installation in Lightroom Classic.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PLUGIN_DIR="$PROJECT_ROOT/plugin/StyleAI.lrdevplugin"
BUILD_DIR="$PROJECT_ROOT/build"

echo "=== StyleAI Plugin Packager ===" >&2

# Check plugin directory exists
if [[ ! -d "$PLUGIN_DIR" ]]; then
    echo "ERROR: Plugin directory not found: $PLUGIN_DIR" >&2
    exit 1
fi

# Create build directory
mkdir -p "$BUILD_DIR"

# Version from Info.lua
MAJOR=$(grep "Info.MAJOR" "$PLUGIN_DIR/Info.lua" | head -1 | sed 's/.*= //' | tr -d ' ')
MINOR=$(grep "Info.MINOR" "$PLUGIN_DIR/Info.lua" | head -1 | sed 's/.*= //' | tr -d ' ')
REVISION=$(grep "Info.REVISION" "$PLUGIN_DIR/Info.lua" | head -1 | sed 's/.*= //' | tr -d ' ')
VERSION="${MAJOR}.${MINOR}.${REVISION}"
echo "Packaging StyleAI v$VERSION..." >&2

# Create zip
ZIP_NAME="StyleAI-Plugin-v${VERSION}.zip"
ZIP_PATH="$BUILD_DIR/$ZIP_NAME"

# Clean up any old zip
rm -f "$ZIP_PATH"

# Create zip from plugin directory
cd "$PROJECT_ROOT/plugin"
zip -r "$ZIP_PATH" "StyleAI.lrdevplugin" \
    -x "*.DS_Store" \
    -x "*/.git*" \
    -x "*/__pycache__/*" \
    -x "*.pyc" \
    -x "*.swp"

# Verify
if [[ -f "$ZIP_PATH" ]]; then
    SIZE=$(du -h "$ZIP_PATH" | cut -f1)
    echo "✓ Created: $ZIP_PATH ($SIZE)" >&2
    echo ""
    echo "Installation instructions:" >&2
    echo "  1. Unzip $ZIP_NAME" >&2
    echo "  2. Copy StyleAI.lrdevplugin to:" >&2
    echo "     ~/Library/Application Support/Adobe/Lightroom/Modules/ (macOS)" >&2
    echo "     %APPDATA%\Adobe\Lightroom\Modules\ (Windows)" >&2
    echo "  3. Restart Lightroom Classic" >&2
    echo "  4. Plugin will appear in File > Plug-in Manager" >&2
else
    echo "ERROR: Failed to create zip" >&2
    exit 1
fi

# Also create a combined package with backend (if backend exists)
BACKEND_DIR="$PROJECT_ROOT/server"
if [[ -d "$BACKEND_DIR" ]]; then
    COMBINED_ZIP="$BUILD_DIR/StyleAI-Full-v${VERSION}.zip"
    rm -f "$COMBINED_ZIP"
    
    cd "$PROJECT_ROOT"
    zip -r "$COMBINED_ZIP" \
        "plugin/StyleAI.lrdevplugin" \
        "server/" \
        -x "*.DS_Store" \
        -x "*/.git*" \
        -x "*/__pycache__/*" \
        -x "*.pyc" \
        -x "*.swp" \
        -x "*/.venv/*" \
        -x "*/test/*" \
        -x "*/.pytest_cache/*"
    
    if [[ -f "$COMBINED_ZIP" ]]; then
        SIZE=$(du -h "$COMBINED_ZIP" | cut -f1)
        echo "✓ Created full package: $COMBINED_ZIP ($SIZE)" >&2
    fi
fi

echo "" >&2
echo "Done! Packages are in: $BUILD_DIR" >&2
