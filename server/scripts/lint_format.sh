#!/bin/bash
set -euo pipefail

# ensure no linting errors
echo "Checking for linting errors..."
uv run ruff check src test

echo "Checking for format issues..."
uv run ruff format --check src test
