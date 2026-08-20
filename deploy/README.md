# AMTSM Docker 部署

用 Docker Compose 同时启动 **backend**、**frontend**、**nginx**。浏览器只访问 nginx；页面走 `/`，接口和 SSE 走同源 `/api`。

```
浏览器 ──HTTP :80──► nginx
                      ├── /      → frontend:3000
                      └── /api/  → backend:8000
                                    └── SQLite: 宿主机 SQLITE_DATA_DIR ↔ 容器 /data/amtsm.db
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

本地开发和 Docker 用两套文件，不要互相复制。

- 本地 `uv` / `pnpm dev`：`backend/.env.example`、`frontend/.env.example`
- Docker：本目录的 `.env.backend.example`、`.env.example`

```bash
cd deploy
cp .env.backend.example .env.backend
```

编辑 `.env.backend`：

- 填写 `WECHAT_*`、`SMTP_*`、`NOTIFY_CHANNELS`
- 若从非 `http://localhost` 打开界面（例如 `http://192.168.x.x` 或改了 `HTTP_PORT` 后的 `http://localhost:8080`），把该 origin 加入 `CORS_ORIGINS`（逗号分隔，需带 scheme）
- 不要改 `SQLITE_PATH`，必须保持 `/data/amtsm.db`。数据库在宿主机，不在镜像里；改了这个路径会写进容器层，重建即丢失

前端容器**不读取** `frontend/.env`。`NEXT_PUBLIC_API_BASE_URL` 是构建参数，默认空字符串，浏览器通过 nginx 同源请求 `/api`。

可选：改 nginx 对外端口或 SQLite 宿主机目录：

```bash
cp .env.example .env
# 编辑 HTTP_PORT=8080
# 编辑 SQLITE_DATA_DIR=/var/lib/amtsm  （默认 ./data，相对本目录）
```

Compose 会读取：

| 文件 | 用途 |
|------|------|
| `.env.backend.example` + `.env.backend` | 后端运行时（后者覆盖前者） |
| 前端构建参数 | `NEXT_PUBLIC_API_BASE_URL`（默认空 = 同源 `/api`） |
| `.env` | `HTTP_PORT`、`SQLITE_DATA_DIR`（及可选的 `NEXT_PUBLIC_API_BASE_URL`） |

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
docker compose down          # 停止并删除容器，保留宿主机 SQLite 目录
docker compose down -v       # 同样不会删除 SQLITE_DATA_DIR（已不再使用 named volume）
```

增量重建（尽量复用缓存，日常改代码后用这个）：

```bash
docker compose up -d --build
```

改了 `NEXT_PUBLIC_API_BASE_URL` 必须重新构建前端镜像（该值在构建时打进包）。Docker 默认用空字符串。若要强制指定：

```bash
NEXT_PUBLIC_API_BASE_URL= docker compose up -d --build frontend
```

## 4. 删除并重建镜像

`--build` 会复用 Docker 层缓存。依赖没装上、Dockerfile / lock 改了但镜像还是旧的、或怀疑缓存脏了时，需要先删镜像再无缓存重建。

**不会丢掉数据：** SQLite 在宿主机 `SQLITE_DATA_DIR`（默认 `deploy/data`），删镜像、删容器都不会动这份文件。

在 `deploy/` 下执行。

### 只重建某一个服务

```bash
docker compose build --no-cache backend    # 或 frontend
docker compose up -d --force-recreate backend
```

### 删除本项目构建的镜像后全量重建

`local` 只删 Compose 构建出来的 `amtsm-backend`、`amtsm-frontend`，不删官方 `nginx:1.27-alpine`。

```bash
docker compose down --rmi local
docker compose build --no-cache
docker compose up -d
```

也可以写成一条：

```bash
docker compose down --rmi local && docker compose build --no-cache && docker compose up -d
```

不要用 `up -d --build` 代替 `build --no-cache`：即使镜像已经删掉，前者仍可能命中构建缓存，不保证从头安装依赖。

### 连官方 nginx 镜像一起删掉再拉

仅在 nginx 镜像本身损坏或要强制换新层时使用：

```bash
docker compose down --rmi all
docker compose up -d --build --pull always
```

`--rmi all` 会删掉 `nginx:1.27-alpine`。本机其他项目若共用该标签，下次启动会重新拉取。

### 确认镜像已删干净（可选）

```bash
docker compose images
docker images | grep -E 'amtsm|nginx'
```

`down --rmi` 之后 `amtsm-backend` / `amtsm-frontend` 应不再出现。残留的 `<none>` 悬挂层可清理：

```bash
docker image prune -f
```

不要对整个 Docker 使用 `docker system prune -a`，除非你明确要删掉本机**所有**未使用镜像。

## 数据持久化

SQLite **单独放在宿主机目录**，通过 bind mount 挂进 backend 容器。镜像重建、容器删除重建都不会覆盖这份文件。

| 位置 | 路径 |
|------|------|
| 宿主机（可改） | `SQLITE_DATA_DIR`，默认 `deploy/data`（相对本目录即 `./data`） |
| 容器内（不要改） | `/data/amtsm.db`（`SQLITE_PATH`） |
| 实际文件 | `amtsm.db`、`amtsm.db-wal`、`amtsm.db-shm` |

配置方式：复制 `.env.example` 为 `.env` 后设置，例如：

```bash
SQLITE_DATA_DIR=./data
# 或绝对路径：
# SQLITE_DATA_DIR=/var/lib/amtsm
```

Compose 对应挂载为 `${SQLITE_DATA_DIR:-./data}:/data`。该目录不进镜像，也不由 named volume 管理，`deploy/data/` 已在仓库 `.gitignore` 中忽略。

**不会丢掉数据：**

- `docker compose up -d --build`（重建镜像、重建容器）
- `docker compose down` / `docker compose down -v`
- 删除并重新创建 backend 容器

**会丢掉数据：** 手动删除 `SQLITE_DATA_DIR` 里的文件。

若以前用过 named volume `amtsm-data`，先拷到新目录再启动：

```bash
mkdir -p data
docker run --rm -v amtsm_amtsm-data:/from -v "$(pwd)/data:/to" alpine cp -a /from/. /to/
```

## 本目录文件

| 文件 | 说明 |
|------|------|
| `docker-compose.yml` | 三服务编排、健康检查、SQLite 宿主机目录挂载 |
| `Dockerfile.backend` | Python 3.12-slim + uv |
| `Dockerfile.frontend` | Node 22 + pnpm standalone |
| `nginx.conf` | 反代；`/api/` 关闭缓冲以支持 SSE |
| `.env.example` | `HTTP_PORT`、`SQLITE_DATA_DIR` 模板 |
| `.env.backend.example` | Docker 后端运行时模板 |
| `data/` | 默认 SQLite 宿主机目录（启动后自动出现，已 gitignore） |

时区均为 `Asia/Shanghai`（与 A 股交易时段、调度任务一致）。当前不包含 HTTPS；需要证书时再在 nginx 前加一层或自行扩展配置。
