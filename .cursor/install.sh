#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root relative to this script (.cursor/install.sh).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Install uv (Python dependency manager) if it is not already available.
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# Backend: create/refresh the virtualenv from the pinned lockfile.
cd "$REPO_ROOT/backend"
uv sync

# Frontend: install workspace dependencies from the pinned lockfile.
cd "$REPO_ROOT"
pnpm install --frozen-lockfile
