#!/usr/bin/env bash
# Build a distributable macOS Apple silicon release from this checkout.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'HELP'
Usage: bash scripts/build-local-release.sh [--version LABEL] [--output DIRECTORY]

Builds an Apple silicon macOS ZIP and SHA-256 checksum. By default the label is
build-<commit> (with -dirty for uncommitted changes), and output is dist/release.
A label beginning with v must match the checked-in release version. Existing
archives with the same label are replaced. Requires uv, Git, lua, luac, and
free loopback port 19819.

Set STYLEAI_MACOS_CODESIGN_IDENTITY to a Keychain signing identity to sign
the backend, including an Apple Development identity for local builds.
For notarization, use a Developer ID Application identity and also set
STYLEAI_MACOS_NOTARY_PROFILE to a notarytool Keychain profile.
HELP
}

VERSION=""
OUTPUT="$ROOT_DIR/dist/release"
while (($#)); do
  case "$1" in
    --version|--output)
      if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
        echo "Missing value for $1" >&2; exit 2
      fi
      if [[ "$1" == --version ]]; then VERSION="$2"; else OUTPUT="$2"; fi
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "This script must run natively on an Apple silicon Mac." >&2; exit 1
fi
command -v uv >/dev/null || { echo "uv is required; see scripts/setup-local-uv-env.sh." >&2; exit 1; }
command -v git >/dev/null || { echo "Git is required." >&2; exit 1; }
for tool in lua luac; do
  command -v "$tool" >/dev/null || { echo "$tool is required for release validation." >&2; exit 1; }
done
NOTARY_ARGS=()
if [[ -n "${STYLEAI_MACOS_NOTARY_PROFILE:-}" ]]; then
  if [[ -z "${STYLEAI_MACOS_CODESIGN_IDENTITY:-}" ]]; then
    echo "Set STYLEAI_MACOS_CODESIGN_IDENTITY to a Developer ID Application identity for notarization." >&2
    exit 1
  fi
  NOTARY_ARGS=(--notary-profile "$STYLEAI_MACOS_NOTARY_PROFILE")
fi
if [[ -z "$VERSION" ]]; then
  VERSION="build-$(git -c core.fsmonitor=false rev-parse --short=12 HEAD)"
  if [[ -n "$(git -c core.fsmonitor=false status --porcelain --untracked-files=normal)" ]]; then
    VERSION="${VERSION}-dirty"
  fi
fi
if [[ ! "$VERSION" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  echo "Invalid version label; use only letters, numbers, dots, underscores and hyphens." >&2; exit 2
fi
if [[ "$VERSION" == v* ]]; then
  python3 scripts/validate_release_version.py "$VERSION"
fi
# uv-managed Python lives in the checkout, independently of Conda/system Python.
export UV_PYTHON_INSTALL_DIR="$ROOT_DIR/server/.python-managed"
uv sync --project "$ROOT_DIR/server" --locked --dev --managed-python
uv run --project "$ROOT_DIR/server" --locked python scripts/validate_lrc_plugin.py
uv run --project "$ROOT_DIR/server" --locked python -c 'from scripts.smoke_release_backend import check_port; check_port()'

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/styleai-build.XXXXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
export PYINSTALLER_CONFIG_DIR="$WORK_DIR/pyinstaller-cache"
uv run --project "$ROOT_DIR/server" --locked pyinstaller styleai_server.spec \
  --noconfirm --distpath "$WORK_DIR/dist" --workpath "$WORK_DIR/work"
uv run --project "$ROOT_DIR/server" --locked python scripts/smoke_release_backend.py \
  "$WORK_DIR/dist/styleai-server/styleai-server"
uv run --project "$ROOT_DIR/server" --locked python scripts/package_release.py \
  --backend "$WORK_DIR/dist/styleai-server" --platform macos-arm64 \
  --version="$VERSION" --output="$OUTPUT" ${NOTARY_ARGS[@]+"${NOTARY_ARGS[@]}"}
echo "Checksum: $OUTPUT/StyleAI-macos-arm64-${VERSION}.zip.sha256"
