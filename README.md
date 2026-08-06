# AMTSM

A-share trading signal monitor and alert system.

## Stack
- Backend: Python 3.12, FastAPI, APScheduler, SQLite
- Frontend: Next.js, TypeScript, pnpm
- Storage: SQLite with WAL mode

## Monorepo Layout
- docs/: product and design docs
- backend/: API service, scheduler and engine skeleton
- frontend/: web UI skeleton
- scripts/: local start scripts

## Quick Start
### 1) Backend
1. Install uv and Python 3.12.
2. Run in backend directory:
   - uv sync
   - uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

### 2) Frontend
1. Run in frontend directory:
   - pnpm install
   - pnpm dev
2. Open http://localhost:3000

## WeChat Channel (企业微信告警)
买卖点告警依赖企业微信应用消息（wechatpy）。配置步骤：

1. 在企业微信管理后台创建应用，记录 CorpID、AgentId、Secret。
2. 确认收件人已加入企业并已知 UserID。
3. 复制 `.env.example` 为 `backend/.env`（或仓库根 `.env`），填写：
   - `WECHAT_CORP_ID`
   - `WECHAT_AGENT_ID`
   - `WECHAT_SECRET`
   - `WECHAT_TO_USER`（多用户用 `|` 分隔，如 `zhangsan|lisi`，或 `@all`）
4. 依赖已包含在 `uv sync` 中（`wechatpy[cryptography]`）。
5. 启动后端时会自动做一次连通性自检（发送测试文本）；失败不会拖垮进程，通道标记为不可用。
6. 查询状态：`GET /api/notify/wechat/status`；手动自检：`POST /api/notify/wechat/self-check`。

Secret 不会出现在状态接口或普通业务日志中。

## Notes
- SQLite file path defaults to data/amtsm.db
- Database schema is initialized on API startup
- WAL mode is enabled during initialization
