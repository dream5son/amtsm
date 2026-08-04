#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
cd backend

uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
