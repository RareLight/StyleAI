#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_DIR="${ROOT_DIR}/docs/wiki"

mkdir -p "${WIKI_DIR}"

ROOT_README="${ROOT_DIR}/README.md"

write_generated_page() {
  local target_file="$1"
  local source_file="$2"
  local title="$3"

  {
    echo "# ${title}"
    echo
    echo "> Auto-generated from \`${source_file#${ROOT_DIR}/}\`. Do not edit this page manually."
    echo
    cat "${source_file}"
  } > "${target_file}"
}

if [[ -f "${ROOT_README}" ]]; then
  write_generated_page "${WIKI_DIR}/Project-README.md" "${ROOT_README}" "Project README"
fi

echo "Generated wiki pages from repository README files."
