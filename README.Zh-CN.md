# AMTSM

[English](README.md) | [中文](README.Zh-CN.md)

A 股交易信号监控与提醒系统。

## 目标

面向无法全天盯盘的有经验 A 股投资者：

- 监控个人自选股，基于 N 日高低点区间策略提示买卖机会。
- 跟踪已登记持仓，在止损 / 止盈时发出提醒，避免亏损扩大、利润回吐。
- 用历史数据回测参数组合（核心持仓还可对比买入持有），让设置有据可依，而不是凭感觉。
- 通过微信和/或邮件送达提醒；交易仍由用户自行下单。

本项目**不会**自动下单，也不会同步券商持仓。

## 技术栈
- 后端：Python 3.12、FastAPI、APScheduler、SQLite
- 前端：Next.js、TypeScript、pnpm
- 存储：SQLite（WAL 模式）

## 仓库结构
- docs/：产品与设计文档
- backend/：API 服务、调度与引擎骨架
- frontend/：Web UI 骨架
- deploy/：Docker Compose、Dockerfile 与 nginx 配置
- scripts/：本地启动脚本

## 快速开始
### 1) 后端
1. 安装 uv 和 Python 3.12。
2. 在 backend 目录执行：
   - uv sync
   - uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

### 2) 前端
1. 在 frontend 目录执行：
   - pnpm install
   - pnpm dev
2. 打开 http://localhost:3000

### 3) Docker（后端 + 前端 + nginx）
镜像锁定 **Python 3.12**、**Node 22** 和 **pnpm 11.20.0**。Python 依赖来自 `backend/uv.lock`（`uv sync --frozen --no-dev`）；Node 依赖来自 `pnpm-lock.yaml`（`pnpm install --frozen-lockfile`）。不要使用 `python:latest` / `node:latest`，也不要在镜像里做无 lock 的 `pip` / `npm` 安装。

环境变量仍放在各自应用目录（Compose 会读取；`deploy/.env` 只负责 nginx 端口）：

1. 将 `backend/.env.example` 复制为 `backend/.env`，填写微信 / SMTP（若从非 `localhost` 打开界面，还要改 `CORS_ORIGINS`）。
2. 将 `frontend/.env.example` 复制为 `frontend/.env`，供本地 `pnpm dev` 使用。Docker 构建时使用空的 `NEXT_PUBLIC_API_BASE_URL`，浏览器通过 nginx 同源访问 `/api`。
3. 如需改端口，将 `deploy/.env.example` 复制为 `deploy/.env` 并设置 `HTTP_PORT`。
4. 在 `deploy/` 目录执行：
   - `docker compose up -d --build`
5. 打开 http://localhost（或 `http://localhost:$HTTP_PORT`）。

对外只暴露 nginx。`/` 反代到 Next.js，`/api/`（含 SSE）反代到 FastAPI。SQLite 保存在 `amtsm-data` 数据卷的 `/data/amtsm.db`。

## 通知渠道

买卖提醒会通过 `NOTIFY_CHANNELS` 中列出的文本渠道发出（逗号分隔，默认 `wechat`）。有效值：`wechat`、`email`，或 `wechat,email`。只要所选渠道中至少有一个发送成功，该提醒即记为成功。

### 微信（企业微信）
买卖提醒可通过 wechatpy 以企业微信应用消息发出。配置步骤：

1. 在企业微信管理后台创建应用，记下 CorpID、AgentId 和 Secret。
2. 确保接收人已加入企业，并知晓其 UserID。
3. 将 `.env.example` 复制为 `backend/.env`（或仓库根目录的 `.env`），并设置：
   - `WECHAT_CORP_ID`
   - `WECHAT_AGENT_ID`
   - `WECHAT_SECRET`
   - `WECHAT_TO_USER`（多个用户用 `|` 分隔，例如 `zhangsan|lisi`，也可使用 `@all`）
4. 依赖已包含在 `uv sync` 中（`wechatpy[cryptography]`）。
5. 若 `NOTIFY_CHANNELS` 包含 `wechat` 且 `WECHAT_SELF_CHECK_ON_STARTUP=true`，后端启动时会发送连通性自检（一条测试文本消息）。失败不会导致进程退出；该渠道会被标记为不可用。
6. 状态查询：`GET /api/notify/wechat/status`。手动自检：`POST /api/notify/wechat/self-check`。

Secret 不会出现在状态 API 或普通应用日志中。

### 邮件（SMTP）
1. 设置 `SMTP_HOST`、`SMTP_FROM` 和 `SMTP_TO`（多个收件人用逗号分隔）。
2. 登录名为 `SMTP_USERNAME`（也接受 `SMTP_USER`）。若未设置且已配置密码，则使用 `SMTP_FROM`。126/QQ 等公共邮箱需要登录；密码通常是客户端授权码，而不是邮箱登录密码。
3. 端口 `587` 使用 STARTTLS（`SMTP_USE_TLS=true`）。端口 `465` 会自动使用隐式 SSL（无需设置 `SMTP_USE_SSL`）。在 465 上使用明文 SMTP 会一直等到超时。
4. 若 `NOTIFY_CHANNELS` 包含 `email` 且 `SMTP_SELF_CHECK_ON_STARTUP=true`，启动时会发送自检邮件。失败不会导致进程退出。
5. 状态查询：`GET /api/notify/email/status`。手动自检：`POST /api/notify/email/self-check`。管理界面：`/admin/email`。

## 说明
- SQLite 文件路径默认为 data/amtsm.db
- 数据库 schema 在 API 启动时初始化
- 初始化时会启用 WAL 模式
