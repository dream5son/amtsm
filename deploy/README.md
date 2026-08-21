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

之后改代码或配置，走第 3 节。`.env.backend` 已填过就不要再 `cp` 覆盖。

## 3. 代码 / 配置变更后如何部署

源码和依赖打进镜像，改完必须 `--build`；运行时配置（`.env.backend`、端口、`nginx.conf`）一般只重建容器或 reload，不必重建镜像。SQLite 在宿主机，下面操作都不会丢数据。

一律在 `deploy/` 下执行。若机器上的代码不是刚改的，先 `git pull`。

不确定改了什么、或一次改了多处时，用这一条即可：

```bash
cd deploy
docker compose up -d --build
```

Compose 会按变更重建镜像、重建容器并启动。已有层缓存会复用，日常更新够用。

### 按改动选命令

| 改了什么 | 要不要重建镜像 | 命令 |
|----------|----------------|------|
| `backend/app/` 源码 | 是（backend） | `docker compose up -d --build backend` |
| `backend/pyproject.toml` / `uv.lock` | 是（backend） | 同上；依赖没装上见第 5 节无缓存重建 |
| `frontend/` 页面、组件、样式 | 是（frontend） | `docker compose up -d --build frontend` |
| `pnpm-lock.yaml` / `frontend/package.json` | 是（frontend） | 同上；依赖没装上见第 5 节 |
| `deploy/.env` 里的 `NEXT_PUBLIC_API_BASE_URL` | 是（frontend，构建期写入包内） | `docker compose up -d --build frontend` |
| `deploy/.env.backend`（微信、SMTP、`CORS_ORIGINS`、轮询间隔等） | 否 | `docker compose up -d --force-recreate backend` |
| `deploy/.env` 里的 `HTTP_PORT` / `SQLITE_DATA_DIR` | 否 | `docker compose up -d` |
| `deploy/nginx.conf` | 否（已只读挂载进容器） | `docker compose exec nginx nginx -s reload` |
| `deploy/Dockerfile.*` / `docker-compose.yml` | 视变更 | `docker compose up -d --build` |

不要用 `docker compose restart` 加载新环境变量：`restart` 只重启现有容器，不会重新读 `.env.backend` 或端口映射。改配置用 `up -d`（必要时加 `--force-recreate`）。

### 只改后端运行时配置

编辑 `deploy/.env.backend` 后：

```bash
docker compose up -d --force-recreate backend
```

容器会用新变量启动，数据库文件不动。改了 `CORS_ORIGINS` 后，用实际打开界面的 origin 再访问一次确认。

### 只改 nginx

`nginx.conf` 已挂载，改完 reload 即可，不必重建镜像：

```bash
docker compose exec nginx nginx -s reload
```

reload 失败再 `docker compose restart nginx`。若改了 `HTTP_PORT`，必须 `docker compose up -d` 才能改端口映射。

### 前端 API 地址

`NEXT_PUBLIC_API_BASE_URL` 在**构建镜像时**打进前端，容器启动后再改 `deploy/.env` 无效。Docker 默认空字符串（浏览器走 nginx 同源 `/api`）。只有要强制指定时才构建：

```bash
NEXT_PUBLIC_API_BASE_URL= docker compose up -d --build frontend
```

### 更新后核对

```bash
docker compose ps
curl -sS http://localhost/api/health    # 若改了 HTTP_PORT，换成对应端口
```

三个服务应为 `healthy`。有问题看日志：`docker compose logs -f backend`（或 `frontend` / `nginx`）。

依赖改了但镜像行为仍像旧的、或 `--build` 后依赖没装上，再做第 5 节的无缓存重建。

## 4. 日常命令

均在 `deploy/` 下执行：

```bash
docker compose ps
docker compose logs -f
docker compose logs -f backend
docker compose restart backend   # 只重启进程，不重新读 env / 不重建镜像
docker compose down              # 停止并删除容器，保留宿主机 SQLite 目录
docker compose down -v           # 同样不会删除 SQLITE_DATA_DIR（已不再使用 named volume）
```

## 5. 删除并重建镜像

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

## 排障：运行一天后 CPU 飙高

### 现象

Docker Compose 跑一段时间（常见过夜）后 CPU 明显升高。nginx access log 里大量出现：

```text
127.0.0.1 ... "GET /api/health HTTP/1.1" 200 34 "-" "Wget" "-"
```

间隔约 10～30 秒，看起来像健康检查把机器打满。

### 结论：健康检查不是根因

这些请求来自 **Docker HEALTHCHECK**（nginx 容器内 `wget` 打本机 `/api/health`），不是公网流量：

- `User-Agent: Wget`、来源 `127.0.0.1`
- 当前 compose 探活间隔为 **30s**（历史上曾为 10s）
- 每次立刻 `200`，体量约 34 字节，**撑不起 CPU 飙高**

市场收盘后日志里几乎只剩探活行，所以容易误判。真正需要怀疑的是：浏览器经 **ngrok**（或其它隧道）长时间挂着首页时，**SSE 长连接泄漏 / 重连堆积**。

### 更可能的根因

首页会立刻打开两条永不结束的 `EventSource`：

| 流 | 路径 | 服务端行为 |
|----|------|------------|
| 系统状态 | `/api/system/status/stream` | 轮询内存状态 + 心跳 |
| 调度活动 | `/api/jobs/activity/stream` | 轮询 activity buffer；调度器元数据定期 `get_jobs()` |

叠加因素：

1. **ngrok / 浏览器重连**：免费隧道对长连接不稳定；`EventSource` 默认约 3s 自动重连。上游到 backend 的旧连接不一定立刻关掉，`while True` 生成器会继续空转。
2. **nginx 长超时**：`/api/` 曾设 `proxy_read_timeout 3600s`，死连接可挂很久；现已降到约 **90s**（仍大于约 30s 的 SSE 心跳）。
3. **回测 worker 空闲刷日志**：历史上每 3s 打一条 INFO idle，会进 activity 面板再经 SSE 推前端；现已改为 DEBUG，空闲间隔默认 **10s**。

堆积后 CPU 大致随残留 SSE 连接数线性上升，因此往往「跑了一天才明显」。

### 已做的缓解（代码 / 配置）

- SSE 生成器每轮检测 `request.is_disconnected()`，断开即退出
- activity 轮询放宽；调度器视图不再每个 tick 都 `get_jobs()`
- nginx：`location = /api/health` 关闭 access_log；`/api/` 读超时约 90s
- compose：健康检查 30s；各服务 json-file 日志轮转（`10m × 3`）
- 回测 idle 仅 DEBUG，避免空转刷面板

### 如何确认

服务起来后：

```bash
docker stats
# backend 容器内看 8800 上 ESTABLISHED 数量（页面开着时大约几条 SSE，不应随时间涨到成百上千）
docker compose exec backend sh -c "ss -tn state established '( sport = :8800 )' | wc -l"
```

正常：少量 ESTABLISHED（探活瞬时 + 页面约 2 条 SSE）。若连接数随 ngrok 标签页过夜持续上涨，仍可能是泄漏，需再查。

### 使用建议

- ngrok-free **不适合**长期挂 SSE；对外尽量用固定域名，或至少不要让标签页过夜挂着隧道地址。
- 若 `docker stats` 高 CPU 在 frontend 而非 backend，再单独排查 Next.js 过夜内存 / GC，与上述 SSE 路径无关。
