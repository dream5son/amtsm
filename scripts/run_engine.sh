#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
cd backend

# Placeholder: scheduler also starts in API lifespan for now.
uv run python -m app.engine.tasks
