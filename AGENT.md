# AGENT.md

## Project Overview

**AMTSM** (A-share Trading Signal Monitor) is a monorepo web application that monitors A-share (Chinese stock market) trading signals and sends alerts. It consists of a Python FastAPI backend and a Next.js frontend.

## Repository Layout

```
amtsm/
├── backend/          # Python FastAPI service + APScheduler engine
│   ├── app/
│   │   ├── api/      # Route handlers (health, stocks, watchlist, strategy)
│   │   ├── db/       # SQLite init and access
│   │   ├── engine/   # Scheduler and signal computation
│   │   ├── schemas/  # Pydantic models
│   │   ├── services/ # Business logic
│   │   ├── config.py # Settings via pydantic-settings
│   │   └── main.py   # FastAPI app entry point
│   ├── tests/        # pytest test suite
│   └── pyproject.toml
├── frontend/         # Next.js 15 + TypeScript + Tailwind
│   ├── app/          # Next.js App Router pages
│   ├── components/   # Reusable React components
│   └── lib/          # Utility functions
├── docs/             # PRD, design doc, user stories
├── scripts/          # Shell scripts to start services locally
├── data/             # SQLite database files (gitignored)
└── pnpm-workspace.yaml
```

## Tech Stack

- **Backend**: Python 3.12, FastAPI, APScheduler, SQLite (WAL mode), akshare, wechatpy
- **Frontend**: Next.js 15, React 19, TypeScript, Tailwind CSS 4, pnpm
- **Package manager (frontend)**: pnpm (see `.npmrc` and `pnpm-workspace.yaml`)
- **Python tooling**: uv for dependency management and running commands

## Development Commands

### Backend

All backend commands run from the `backend/` directory.

```bash
# Install dependencies
uv sync

# Start API server (dev, with reload)
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest

# Lint / format
uv run ruff check .
uv run ruff format .
```

### Frontend

All frontend commands run from the `frontend/` directory.

```bash
# Install dependencies
pnpm install

# Start dev server (http://localhost:3000)
pnpm dev

# Production build
pnpm build

# Lint
pnpm lint
```

### Using the helper scripts (from repo root)

```bash
bash scripts/run_backend.sh
bash scripts/run_frontend.sh
bash scripts/run_engine.sh
```

## Environment Variables

Copy `.env.example` to `.env` in both `backend/` and `frontend/` and fill in values. Key settings:

- `SQLITE_PATH` — path to the SQLite database file (default: `data/amtsm.db`)
- `API_HOST` / `API_PORT` — backend bind address (default: `0.0.0.0:8000`)
- `NOTIFY_CHANNELS` — comma-separated text channels (`wechat`, `email`; default: `wechat`)
- `WECHAT_CORP_ID` / `WECHAT_AGENT_ID` / `WECHAT_SECRET` / `WECHAT_TO_USER` — enterprise WeChat channel (see README)
- `WECHAT_SELF_CHECK_ON_STARTUP` — run WeChat connectivity self-check on API boot (default: true)
- `SMTP_HOST` / `SMTP_FROM` / `SMTP_TO` — SMTP email channel (see README)
- `SMTP_SELF_CHECK_ON_STARTUP` — run email connectivity self-check on API boot (default: true)

## Architecture Notes

- **Database**: SQLite with WAL mode, initialized automatically on API startup via `app.db.init_db`.
- **Scheduler**: APScheduler starts within the FastAPI lifespan context; it drives intraday polling, daily baseline precompute, and snapshot jobs.
- **Signal engine**: Located in `backend/app/engine/`; consumes market data from akshare and evaluates buy/sell strategy parameters stored per watchlist entry.
- **Alerts**: Text notifications go through `TextNotifier` implementations (enterprise WeChat and/or SMTP email) selected by `NOTIFY_CHANNELS`; throttling and DND windows are enforced by the alert service.
- **CORS**: Backend allows requests from `http://localhost:3000` only by default.

## Testing

- Backend tests live in `backend/tests/` and use `pytest`.
- Run all tests: `cd backend && uv run pytest`
- There are no frontend tests configured yet.

## Key Conventions

- Python: follow `ruff` defaults (line length 88, PEP 8).
- TypeScript: strict mode enabled (`tsconfig.json`).
- Pydantic models in `backend/app/schemas/`; keep request/response schemas separate from DB models.
- API routes are registered in `backend/app/main.py`; add new routers there.
- New frontend pages go under `frontend/app/` following Next.js App Router conventions.
