#!/usr/bin/env bash
# diagnose-styleai-plugin.sh
# Run this to check for common plugin installation issues.

PLUGIN_DIR="${HOME}/Library/Application Support/Adobe/Lightroom/Modules/StyleAI.lrdevplugin"

echo "========================================"
echo "  StyleAI Plugin Diagnostic"
echo "========================================"
echo ""

if [ ! -d "$PLUGIN_DIR" ]; then
    echo "ERROR: Plugin not found at:"
    echo "  $PLUGIN_DIR"
    echo ""
    echo "The plugin is not installed. Run:"
    echo "  bash scripts/styleai-installer.sh install"
    exit 1
fi

echo "Plugin location: $PLUGIN_DIR"
echo ""

# 1. Check for missing critical files
echo "=== Checking critical files ==="
MISSING=0
for file in Info.lua MetadataProvider.lua MetadataTagset.lua Init.lua PluginInfo.lua ShutdownApp.lua AiEditAction.lua TaskAnalyzeAndIndex.lua TaskAiEditPredictive.lua TaskTrainFromEdits.lua TaskReviewAIEditOutcome.lua TaskStyleCatalog.lua TaskDiscoverUpgradeCandidates.lua; do
    if [ -f "${PLUGIN_DIR}/${file}" ]; then
        echo "  OK: $file"
    else
        echo "  MISSING: $file"
        MISSING=$((MISSING + 1))
    fi
done
echo ""

# 2. Check for old conflicting plugins
echo "=== Checking for conflicting plugins ==="
CONFLICTS=0
for old in LrGeniusAI.lrplugin LrGeniusAI.lrdevplugin StyleAI.lrplugin; do
    if [ -d "${HOME}/Library/Application Support/Adobe/Lightroom/Modules/${old}" ]; then
        echo "  WARNING: Found old plugin: $old"
        CONFLICTS=$((CONFLICTS + 1))
    fi
done
if [ $CONFLICTS -eq 0 ]; then
    echo "  No conflicting plugins found"
fi
echo ""

# 3. Check file permissions
echo "=== Checking file permissions ==="
UNREADABLE=0
for file in Info.lua MetadataProvider.lua MetadataTagset.lua Init.lua; do
    if [ -r "${PLUGIN_DIR}/${file}" ]; then
        : # readable
    else
        echo "  UNREADABLE: $file"
        UNREADABLE=$((UNREADABLE + 1))
    fi
done
if [ $UNREADABLE -eq 0 ]; then
    echo "  All critical files are readable"
fi
echo ""

# 4. Check for hidden/system files
echo "=== Checking for problematic files ==="
if find "$PLUGIN_DIR" -name ".DS_Store" -o -name "__MACOSX" -o -name "*.pyc" | grep -q .; then
    echo "  WARNING: Found hidden/cache files that may confuse Lightroom"
    find "$PLUGIN_DIR" -name ".DS_Store" -o -name "__MACOSX" -o -name "*.pyc"
else
    echo "  No problematic files found"
fi
echo ""

# 5. Check line endings (should be LF, not CRLF)
echo "=== Checking file line endings ==="
CRLF_FILES=$(find "$PLUGIN_DIR" -name "*.lua" -exec file {} \; | grep "CRLF" || true)
if [ -n "$CRLF_FILES" ]; then
    echo "  WARNING: Found files with Windows line endings:"
    echo "$CRLF_FILES"
else
    echo "  All Lua files use Unix line endings (OK)"
fi
echo ""

# 6. Summary
echo "========================================"
if [ $MISSING -gt 0 ] || [ $CONFLICTS -gt 0 ] || [ $UNREADABLE -gt 0 ]; then
    echo "  DIAGNOSTIC FAILED"
    echo "========================================"
    echo ""
    echo "Please run a clean reinstall:"
    echo "  1. Quit Lightroom Classic completely"
    echo "  2. Run: bash scripts/styleai-installer.sh redeploy"
    echo "  3. Start Lightroom Classic"
    exit 1
else
    echo "  DIAGNOSTIC PASSED"
    echo "========================================"
    echo ""
    echo "The plugin files look correct."
    echo ""
    echo "If Lightroom still shows a schema error:"
    echo "  1. QUIT Lightroom Classic completely"
    echo "  2. Run: bash scripts/styleai-installer.sh redeploy"
    echo "  3. Start Lightroom Classic"
    echo ""
    echo "The most common cause of schema errors is installing"
    echo "while Lightroom is running. Always quit Lightroom first."
fi
