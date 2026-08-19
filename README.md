# AMTSM

A-share trading signal monitor and alert system.

## Goals

Help experienced A-share investors who cannot watch the market all day:

- Monitor a personal watchlist and surface buy/sell opportunities from an N-day high/low range strategy.
- Track registered positions and alert on stop-loss / take-profit so losses do not run and gains are not given back.
- Backtest parameter sets against history (and, for core holdings, against buy-and-hold) so settings are evidence-based rather than guesswork.
- Deliver alerts over WeChat and/or email; the user still trades by hand.

This project does **not** place orders or sync broker positions.

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

## Notification Channels

买卖点告警通过 `NOTIFY_CHANNELS`（逗号分隔，默认 `wechat`）选择文本通道，可填 `wechat`、`email` 或 `wechat,email`。至少一个已选通道发送成功则记为告警成功。

### WeChat（企业微信）
买卖点告警可走企业微信应用消息（wechatpy）。配置步骤：

1. 在企业微信管理后台创建应用，记录 CorpID、AgentId、Secret。
2. 确认收件人已加入企业并已知 UserID。
3. 复制 `.env.example` 为 `backend/.env`（或仓库根 `.env`），填写：
   - `WECHAT_CORP_ID`
   - `WECHAT_AGENT_ID`
   - `WECHAT_SECRET`
   - `WECHAT_TO_USER`（多用户用 `|` 分隔，如 `zhangsan|lisi`，或 `@all`）
4. 依赖已包含在 `uv sync` 中（`wechatpy[cryptography]`）。
5. 若 `NOTIFY_CHANNELS` 包含 `wechat` 且 `WECHAT_SELF_CHECK_ON_STARTUP=true`，启动后端时会自动做一次连通性自检（发送测试文本）；失败不会拖垮进程，通道标记为不可用。
6. 查询状态：`GET /api/notify/wechat/status`；手动自检：`POST /api/notify/wechat/self-check`。

Secret 不会出现在状态接口或普通业务日志中。

### Email（SMTP）
1. 填写 `SMTP_HOST`、`SMTP_FROM`、`SMTP_TO`（多个收件人用逗号分隔）。
2. 登录名用 `SMTP_USERNAME`（也接受 `SMTP_USER`）；未填时若配置了密码，则用 `SMTP_FROM`。126/QQ 等公共邮箱必须登录，密码一般是客户端授权码而非登录密码。
3. 端口 `587` 使用 STARTTLS（`SMTP_USE_TLS=true`）。端口 `465` 自动走隐式 SSL（不必再设 `SMTP_USE_SSL`）。在 465 上用明文 SMTP 会一直等到超时。
4. 若 `NOTIFY_CHANNELS` 包含 `email` 且 `SMTP_SELF_CHECK_ON_STARTUP=true`，启动时会发送一封自检邮件；失败不会拖垮进程。
5. 查询状态：`GET /api/notify/email/status`；手动自检：`POST /api/notify/email/self-check`。前端管理页：`/admin/email`。

## Notes
- SQLite file path defaults to data/amtsm.db
- Database schema is initialized on API startup
- WAL mode is enabled during initialization
