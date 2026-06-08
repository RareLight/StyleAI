#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GITHUB_REPOSITORY:-}" ]]; then
  # Try to infer from git remote
  remote_url=$(git remote get-url origin 2>/dev/null || true)
  if [[ "$remote_url" =~ github\.com[:/](.+/.+)\.git ]]; then
    GITHUB_REPOSITORY="${BASH_REMATCH[1]}"
  elif [[ "$remote_url" =~ github\.com[:/](.+/.+) ]]; then
    GITHUB_REPOSITORY="${BASH_REMATCH[1]}"
  else
    echo "GITHUB_REPOSITORY is required (example: owner/repo) or script must be run inside a cloned github repository"
    exit 1
  fi
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_SRC_DIR="${ROOT_DIR}/docs/wiki"

if [[ ! -d "${WIKI_SRC_DIR}" ]]; then
  echo "Wiki source directory not found: ${WIKI_SRC_DIR}"
  exit 1
fi

# Ensure generated wiki pages from README files are up to date.
if [[ -x "${ROOT_DIR}/scripts/build-wiki-pages.sh" ]]; then
  "${ROOT_DIR}/scripts/build-wiki-pages.sh"
else
  bash "${ROOT_DIR}/scripts/build-wiki-pages.sh"
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  WIKI_REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.wiki.git"
else
  # Fall back to SSH clone if no token is provided, assuming local developer environment
  WIKI_REPO_URL="git@github.com:${GITHUB_REPOSITORY}.wiki.git"
fi

echo "Cloning wiki repo..."
git clone "${WIKI_REPO_URL}" "${TMP_DIR}/wiki"

echo "Syncing docs/wiki -> wiki root..."
rsync -a --delete --exclude ".git" "${WIKI_SRC_DIR}/" "${TMP_DIR}/wiki/"

cd "${TMP_DIR}/wiki"

if [[ -z "$(git status --porcelain)" ]]; then
  echo "No wiki changes to publish."
  exit 0
fi

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  git config user.name "github-actions[bot]"
  git config user.email "github-actions[bot]@users.noreply.github.com"
fi

git add .
git commit -m "docs: sync wiki from docs/wiki"
git push origin master

echo "Wiki publish complete."
