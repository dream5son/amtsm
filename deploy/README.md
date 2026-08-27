# AMTSM Docker 部署

用 Docker Compose 同时启动 **backend**、**frontend**、**nginx**；可选 **cloudflared** 经 Cloudflare Tunnel 把内网栈挂到公网域名。浏览器只访问 nginx（本机 HTTP 或隧道 HTTPS）；页面走 `/`，接口和 SSE 走同源 `/api`。

```
浏览器 ──HTTP :80──► nginx
                      ├── /      → frontend:3800
                      └── /api/  → backend:8800
                                    └── SQLite: 宿主机 SQLITE_DATA_DIR ↔ 容器 /data/amtsm.db

浏览器 ──HTTPS──► Cloudflare Edge ──隧道──► cloudflared ──HTTP──► nginx:80
```

在本目录执行 Compose 命令。构建上下文是仓库根目录。

## 版本锁定


| 组件  | 版本                     | 依赖来源                                           |
| --- | ---------------------- | ---------------------------------------------- |
| 后端  | Python 3.12（非 3.13）    | `backend/uv.lock`（`uv sync --frozen --no-dev`） |
| 前端  | Node 22 + pnpm 11.20.0 | `pnpm-lock.yaml`（`--frozen-lockfile`）          |
| 网关  | nginx 1.27-alpine      | 官方镜像                                           |
| 隧道  | cloudflared 2026.8.2   | 官方镜像 `cloudflare/cloudflared` |


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
- 若从非 `http://localhost` 打开界面（例如 `http://192.168.x.x`、改了 `HTTP_PORT` 后的 `http://localhost:8080`，或 Cloudflare 域名 `https://market-signal.haojianli.me`），把该 origin 加入 `CORS_ORIGINS`（逗号分隔，需带 scheme）
- 不要改 `SQLITE_PATH`，必须保持 `/data/amtsm.db`。数据库在宿主机，不在镜像里；改了这个路径会写进容器层，重建即丢失

前端容器**不读取** `frontend/.env`。`NEXT_PUBLIC_API_BASE_URL` 是构建参数，默认空字符串，浏览器通过 nginx 同源请求 `/api`。

可选：改 nginx 对外端口、SQLite 宿主机目录或日志目录：

```bash
cp .env.example .env
# 编辑 HTTP_PORT=8080
# 编辑 SQLITE_DATA_DIR=/var/lib/amtsm  （默认 ./data，相对本目录）
# 编辑 LOG_DIR=/var/log/amtsm          （默认 ./log，相对本目录）
```

Compose 会读取：


| 文件                                      | 用途                                                             |
| --------------------------------------- | -------------------------------------------------------------- |
| `.env.backend.example` + `.env.backend` | 后端运行时（后者覆盖前者）                                                  |
| 前端构建参数                                  | `NEXT_PUBLIC_API_BASE_URL`（默认空 = 同源 `/api`）                    |
| `.env`                                  | `HTTP_PORT`、`SQLITE_DATA_DIR`、`LOG_DIR`（及可选的 `NEXT_PUBLIC_API_BASE_URL`） |
| `cloudflared/config.yml` + `credentials.json` | 隧道 UUID / 对外域名 / 凭证（从 `*.example` 复制，已 gitignore） |




## 2. 启动

```bash
cd deploy
docker compose up -d --build
```

打开 [http://localhost（若改了端口则为](http://localhost（若改了端口则为) `http://localhost:$HTTP_PORT`）。

健康检查通过后再对外提供服务：

- 后端：`GET /api/health`
- 前端：`GET /`
- nginx：`GET /api/health`

之后改代码或配置，走第 3 节。`.env.backend` 已填过就不要再 `cp` 覆盖。公网 HTTPS 见「Cloudflare Tunnel」。

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


| 改了什么                                                | 要不要重建镜像             | 命令                                              |
| --------------------------------------------------- | ------------------- | ----------------------------------------------- |
| `backend/app/` 源码                                   | 是（backend）          | `docker compose up -d --build backend`          |
| `backend/pyproject.toml` / `uv.lock`                | 是（backend）          | 同上；依赖没装上见第 5 节无缓存重建                             |
| `frontend/` 页面、组件、样式                                | 是（frontend）         | `docker compose up -d --build frontend`         |
| `pnpm-lock.yaml` / `frontend/package.json`          | 是（frontend）         | 同上；依赖没装上见第 5 节                                  |
| `deploy/.env` 里的 `NEXT_PUBLIC_API_BASE_URL`         | 是（frontend，构建期写入包内） | `docker compose up -d --build frontend`         |
| `deploy/.env.backend`（微信、SMTP、`CORS_ORIGINS`、轮询间隔等） | 否                   | `docker compose up -d --force-recreate backend` |
| `deploy/.env` 里的 `HTTP_PORT` / `SQLITE_DATA_DIR` / `LOG_DIR` | 否                   | `docker compose up -d`                          |
| `deploy/nginx.conf`                                 | 否（已只读挂载进容器）         | `docker compose exec nginx nginx -s reload`     |
| `deploy/logging.backend.json` | 否 | `docker compose up -d --force-recreate backend` |
| `deploy/cloudflared/config.yml` / `credentials.json` | 否                 | `docker compose restart cloudflared` |
| `deploy/Dockerfile.*` / `docker-compose.yml`        | 视变更                 | `docker compose up -d --build`                  |


不要用 `docker compose restart` 加载新环境变量：`restart` 只重启现有容器，不会重新读 `.env.backend` 或端口映射。改配置用 `up -d`（必要时加 `--force-recreate`）。`docker compose up -d` 会一并启动 `cloudflared`；凭证未填时该容器会自行重启失败，不影响 nginx / frontend / backend。对外域名见下文「Cloudflare Tunnel」。

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

三个服务应为 `healthy`。有问题看日志：`docker compose logs -f backend`（或 `frontend` / `nginx`），或直接读宿主机 `deploy/log/`。

依赖改了但镜像行为仍像旧的、或 `--build` 后依赖没装上，再做第 5 节的无缓存重建。

## 4. 日常命令

均在 `deploy/` 下执行：

```bash
docker compose ps
docker compose logs -f
docker compose logs -f backend
docker compose logs -f cloudflared
ls -l log/                   # 宿主机外盘：backend / frontend / nginx / cloudflared
docker compose restart backend   # 只重启进程，不重新读 env / 不重建镜像
docker compose down              # 停止并删除容器，保留宿主机 SQLite 与 log 目录
docker compose down -v           # 同样不会删除 SQLITE_DATA_DIR / LOG_DIR（已不再使用 named volume）
```



## 5. 删除并重建镜像

`--build` 会复用 Docker 层缓存。依赖没装上、Dockerfile / lock 改了但镜像还是旧的、或怀疑缓存脏了时，需要先删镜像再无缓存重建。

**不会丢掉数据：** SQLite 在宿主机 `SQLITE_DATA_DIR`（默认 `deploy/data`），日志在 `LOG_DIR`（默认 `deploy/log`）；删镜像、删容器都不会动这些文件。

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


| 位置       | 路径                                                  |
| -------- | --------------------------------------------------- |
| 宿主机（可改）  | `SQLITE_DATA_DIR`，默认 `deploy/data`（相对本目录即 `./data`） |
| 容器内（不要改） | `/data/amtsm.db`（`SQLITE_PATH`）                     |
| 实际文件     | `amtsm.db`、`amtsm.db-wal`、`amtsm.db-shm`            |


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

## 日志持久化

容器 stdout/stderr 仍走 Docker `json-file`（`10m × 3`），`docker compose logs` 照常用。另外把应用日志 bind mount 到宿主机 `LOG_DIR`（默认 `deploy/log`，相对本目录即 `./log`），重建镜像或 `down` 都不会删。

| 位置 | 路径 |
| --- | --- |
| 宿主机（可改） | `LOG_DIR`，默认 `deploy/log` |
| backend | `log/backend/backend.log` |
| frontend | `log/frontend/frontend.log` |
| nginx | `log/nginx/access.log`、`error.log`（同时仍写镜像 stdout/stderr） |
| cloudflared | `log/cloudflared/cloudflared.log`（lumberjack 历史文件同目录） |

滚动：

- 各服务 stdout：Docker `json-file`，`10m × 3`
- backend 宿主机文件：`RotatingFileHandler`，`10MB × 3`
- cloudflared 宿主机文件：`--log-directory` lumberjack，**1MB × 保留 5 个备份**（官方写死）
- frontend / nginx 宿主机文件：只追加，暂不滚动

`deploy/log/` 已 gitignore。不要把外盘挂到 nginx 镜像的 `/var/log/nginx`（那是指向 stdout/stderr 的符号链接）。

配置方式：复制 `.env.example` 为 `.env` 后设置，例如：

```bash
LOG_DIR=./log
# 或绝对路径：
# LOG_DIR=/var/log/amtsm
```



## 本目录文件


| 文件                     | 说明                                   |
| ---------------------- | ------------------------------------ |
| `docker-compose.yml`   | 编排 + 健康检查 + SQLite / log 宿主机目录挂载 |
| `Dockerfile.backend`   | Python 3.12-slim + uv                |
| `Dockerfile.frontend`  | Node 22 + pnpm standalone            |
| `nginx.conf`           | 反代；`/api/` 关闭缓冲以支持 SSE；透传 `X-Forwarded-Proto`；双写 access/error 到宿主机 |
| `logging.backend.json` | uvicorn 同时写 stdout 与 `/var/log/amtsm/backend.log`（`10MB × 3` 滚动） |
| `.env.example`         | `HTTP_PORT`、`SQLITE_DATA_DIR`、`LOG_DIR` 模板     |
| `.env.backend.example` | Docker 后端运行时模板                       |
| `cloudflared/`         | 隧道 `config.yml` / `credentials.json`（仓库只留 `*.example`） |
| `data/`                | 默认 SQLite 宿主机目录（启动后自动出现，已 gitignore） |
| `log/`                 | 默认日志宿主机目录（启动后自动出现，已 gitignore） |


时区均为 `Asia/Shanghai`（与 A 股交易时段、调度任务一致）。本机入口仍是 HTTP；公网 HTTPS 用可选的 Cloudflare Tunnel（见下文），不必在 nginx 上配证书。

## 排障：运行一天后 CPU 飙高

### 现象

`amtsm-backend` 跑大约一天后 CPU 卡在 100% 以上，`/api/health` 仍 200，但股票搜索会 504。`docker compose logs` 可能报：

```text
error from daemon in stream: Error grabbing logs: invalid character '\x00' looking for beginning of value
```

健康检查和首页 SSE **不是**这次的根因。容器里往往只有一个 Python 线程在跑，HTTP 连接数很少，对端 `public-api.baostock.com:10030` 处于 `CLOSE_WAIT`。

### 根因

baostock 登录后把 TCP 长连接挂在进程里。库函数 `send_msg` 是：

```python
while True:
    recv = sock.recv(8192)
    receive += recv
    if receive.endswith(b"<![CDATA[]]>\n"):
        break
```

服务端闲置断开后 `recv()` 立刻返回 `b""`，循环不退出。日快照默认 15:30 会打这条连接，所以经常是收盘后开始飙 CPU。卡住的线程还握着 provider 锁，后续日线 / 搜索全部卡住。

`\x00` 来自 Docker Desktop 的 `json-file` 日志被写坏（常见于进程卡死或轮转），不是应用自己往 stdout 打空字节。

### 已做的修复

- 替换 baostock 的 `connect` / `send_msg`：空 `recv` 视为断线、30s 超时、响应大小封顶、出错关 socket
- 查询遇到 `OSError` 时丢掉会话再登录一次
- 进程退出时关闭 baostock socket
- Compose 日志驱动改为 `local`；APScheduler 执行器日志降到 WARNING

### 如何确认

```bash
docker stats amtsm-backend-1
docker compose exec backend sh -c "cat /proc/net/tcp"
# 10030 端口不应长期停在 CLOSE_WAIT；若 CPU 已飙高，重启才能结束当前空转线程
```

卡住时只能重启进程把空转线程杀掉，补丁只阻止下一次再发生。

## Cloudflare Tunnel（内网对外）

`cloudflared` 只把 Compose 网络里的 **nginx:80** 送到 Cloudflare，不额外映射宿主机端口。凭证以配置文件提供，**不要**写进 Compose `.env`。`docker compose up -d` 会启动该服务；凭证未填时容器会重启失败，其它服务不受影响。

```bash
cd deploy
cp cloudflared/config.yml.example cloudflared/config.yml
cp cloudflared/credentials.json.example cloudflared/credentials.json
# 编辑 config.yml：tunnel UUID、ingress hostname
# 把 Cloudflare 下载的隧道凭证写入 credentials.json（AccountTag / TunnelSecret / TunnelID）
docker compose up -d
```

显式重启隧道：

```bash
docker compose restart cloudflared
```

Cloudflare 侧（locally managed named tunnel）：

1. Zero Trust → Networks → Tunnels → Create a tunnel。优先下载凭证 **JSON 文件** 覆盖 `cloudflared/credentials.json`
2. 若 Dashboard 只给了一段 `eyJ...` **Token**，不要把它整段填进 `TunnelSecret`。解码后写入三个字段：

```bash
python3 -c 'import json,base64,sys; t=sys.argv[1]; pad="="*((4-len(t)%4)%4); d=json.loads(base64.urlsafe_b64decode(t+pad)); print(json.dumps({"AccountTag":d["a"],"TunnelSecret":d["s"],"TunnelID":d["t"]}, indent=2))' '粘贴Token'
```

`AccountTag` 必须是 32 位 Cloudflare 账号 ID（`a`），不是隧道显示名。
3. `config.yml` 的 `tunnel` 填同一 UUID（`t`）；`hostname` 填对外域名（与 `nginx.conf` 的 `server_name` 一致，默认 `market-signal.haojianli.me`）
4. 把该域名 CNAME 到 `<TUNNEL_UUID>.cfargotunnel.com`（或在 Dashboard 绑定 hostname）
5. **Dashboard 创建的隧道是远程配置**：连上后会 `Updated to new configuration` 并**覆盖**本地 `config.yml` 的 ingress。Zero Trust → Networks → Tunnels → 该隧道 → Public Hostname，把源站改成 `http://nginx:80`。不要用 `http://localhost:80`（那是宿主机直连地址；在 cloudflared 容器里 localhost 是它自己，日志会变成 `dial tcp [::1]:80: connection refused`，浏览器 502）。

`config.yml` 与 `credentials.json` 已 gitignore，仓库只保留 `*.example`。

同源 `/api` 经 nginx，前端不必改 `NEXT_PUBLIC_API_BASE_URL`。若浏览器从 `https://market-signal.haojianli.me` 打开，把该 origin 加入 `.env.backend` 的 `CORS_ORIGINS` 后 `docker compose up -d --force-recreate backend`。

### 排错

```bash
docker compose ps
docker compose logs -f cloudflared
curl -sS http://localhost/api/health
curl -sSI https://market-signal.haojianli.me/api/health
```

- 隧道连不上：确认已复制并填写 `cloudflared/config.yml` 与 `credentials.json`，hostname 已 CNAME 到 `<uuid>.cfargotunnel.com`。
- 日志 `Failed to get tunnel` / `control stream encountered a failure`：网络预检全 PASS 时几乎一定是凭证错。常见误填是把 Dashboard Token（`eyJ...`）整段放进 `TunnelSecret`，或把隧道名当成 `AccountTag`。按上一节解码 Token。日志里的 UDP buffer / QUIC 警告可忽略，不是根因。
- 凭证已对但仍频繁切到 `http2`：在 `config.yml` 加 `protocol: http2` 后 `docker compose restart cloudflared`。
- 本机 `http://localhost/api/health` 通、隧道域名 502：看 cloudflared 日志是否 `originService=http://localhost:80` 和 `dial tcp [::1]:80: connection refused`。这是远程配置把源站写成了 localhost。到 Dashboard 把 Public Hostname 源站改成 `http://nginx:80`（改完会再打一条 `Updated to new configuration`，不必重建容器）。
- 隧道已 200 但 curl 被 302 到 `*.cloudflareaccess.com`：hostname 开了 Cloudflare Access；浏览器可登录，直连 API / 健康检查需 Bypass。

## ngrok（不推荐）

长期对外请用上一节的 Cloudflare Tunnel。ngrok-free 对 SSE 不稳定，容易造成重连堆积。仅临时调试时：

[https://dashboard.ngrok.com/get-started/setup/mac-os](https://dashboard.ngrok.com/get-started/setup/mac-os)