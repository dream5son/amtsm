# AMTSM Docker 部署

用 Docker Compose 同时启动 **backend**、**frontend**、**nginx**。浏览器只访问 nginx；页面走 `/`，接口和 SSE 走同源 `/api`。

```
浏览器 ──HTTP :80──► nginx
                      ├── /      → frontend:3000
                      └── /api/  → backend:8000  → SQLite 数据卷 /data
```

在本目录执行 Compose 命令。构建上下文是仓库根目录。

## 版本锁定

| 组件 | 版本 | 依赖来源 |
|------|------|----------|
| 后端 | Python 3.12（非 3.13） | `backend/uv.lock`（`uv sync --frozen --no-dev`） |
| 前端 | Node 22 + pnpm 11.20.0 | `pnpm-lock.yaml`（`--frozen-lockfile`） |
| 网关 | nginx 1.27-alpine | 官方镜像 |

不要改成 `python:latest` / `node:latest`，也不要在镜像里做无 lock 的 `pip` / `npm` 安装。

## 前置条件

- Docker Engine + Docker Compose v2
- 本机 80 端口空闲（或改 `HTTP_PORT`）

## 1. 准备环境变量

应用配置仍放在前后端目录，**不要**把微信 / SMTP 密钥抄到 `deploy/.env`。

```bash
# 仓库根目录
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

编辑 `backend/.env`：

- 填写 `WECHAT_*`、`SMTP_*`、`NOTIFY_CHANNELS`
- 若从非 `localhost` 打开界面（例如 `http://192.168.x.x`），把该 origin 加入 `CORS_ORIGINS`（逗号分隔，需带 scheme）
- `SQLITE_PATH` 在容器内会被覆盖为 `/data/amtsm.db`，本地文件不用改

`frontend/.env` 里的 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` 只给本地 `pnpm dev` 用。镜像构建时使用**空字符串**，浏览器通过 nginx 同源请求 `/api`。

可选：改 nginx 对外端口：

```bash
cp deploy/.env.example deploy/.env
# 编辑 HTTP_PORT=8080
```

Compose 会读取：

| 文件 | 用途 |
|------|------|
| `backend/.env.example` + `backend/.env` | 后端运行时（后者覆盖前者） |
| `frontend/.env.example` + `frontend/.env` | 前端容器运行时 |
| `deploy/.env` | 仅 `HTTP_PORT` |

## 2. 启动

```bash
cd deploy
docker compose up -d --build
```

打开 http://localhost（若改了端口则为 `http://localhost:$HTTP_PORT`）。

健康检查通过后再对外提供服务：

- 后端：`GET /api/health`
- 前端：`GET /`
- nginx：`GET /api/health`

## 3. 日常命令

均在 `deploy/` 下执行：

```bash
docker compose ps
docker compose logs -f
docker compose logs -f backend
docker compose restart backend
docker compose down          # 停止并删除容器，保留数据卷
docker compose down -v       # 同时删除 SQLite 数据卷（不可恢复）
```

重新构建镜像：

```bash
docker compose up -d --build
```

改了 `frontend/.env` 里的 `NEXT_PUBLIC_*` 不会影响已构建镜像；Docker 默认仍用空的 API 基址。若要强制指定构建参数：

```bash
NEXT_PUBLIC_API_BASE_URL= docker compose up -d --build frontend
```

## 数据持久化

SQLite 在 named volume `amtsm-data`，容器路径 `/data/amtsm.db`（含 WAL 文件）。

`docker compose down` 不会删卷。只有 `down -v` 或手动 `docker volume rm` 才会清空数据。

## 本目录文件

| 文件 | 说明 |
|------|------|
| `docker-compose.yml` | 三服务编排、健康检查、数据卷 |
| `Dockerfile.backend` | Python 3.12-slim + uv |
| `Dockerfile.frontend` | Node 22 + pnpm standalone |
| `nginx.conf` | 反代；`/api/` 关闭缓冲以支持 SSE |
| `.env.example` | `HTTP_PORT` 模板 |

时区均为 `Asia/Shanghai`（与 A 股交易时段、调度任务一致）。当前不包含 HTTPS；需要证书时再在 nginx 前加一层或自行扩展配置。
