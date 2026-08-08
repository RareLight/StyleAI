#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(dirname "$SCRIPT_DIR")"
cd "$SERVER_DIR"

# ensure no linting errors
echo "Checking for linting errors..."
uv run ruff check src test

echo "Checking for format issues..."
uv run ruff format --check src test
