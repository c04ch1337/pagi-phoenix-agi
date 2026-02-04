#!/usr/bin/env bash
# Initialize a new app from the Phoenix AGI core template.
# Usage: ./scripts/init-new-app.sh /path/to/new-app
# Run from the template repo root (pagi-phoenix-agi).

set -e
DEST="${1:?Usage: $0 /path/to/new-app}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
rsync -a --exclude='.git' --exclude='*.log' . "$DEST"
cd "$DEST"
git init
echo "New app initialized from pagi template at $DEST"
