针对 **A股交易信号监控告警系统 (V1.0)**，基于 **Python 3 + Next.js + SQLite3** 技术栈，我为您梳理并制定以下系统设计方案，涵盖关键技术点识别、权衡分析及核心架构决策。

---

## 1. 系统整体架构

系统采用 **前后端分离 + 后端引擎异步双驱** 架构：

* **前端（Next.js）**：专注于 UI 渲染、自选股列表展示、策略配置与实时行情刷新（SSR/CSR 结合）。
* **后端 API 服务（Python FastAPI）**：提供 Restful API 给前端调用，管理自选股及配置 CRUD。
* **行情计算与告警引擎（Python Async Engine / APScheduler）**：独立常驻后台，负责盘中实时轮询、高低点策略计算及微信通知触发。
* **数据存储（SQLite3）**：集中存储股票池、策略配置、历史基准值及告警日志。

```
                       +------------------------+
                       |    Next.js Frontend    |
                       +-----------+------------+
                                   | REST API
                                   v
                       +------------------------+
                       |   Python FastAPI Server|
                       +-----------+------------+
                                   | SQLite Access (WAL Mode)
                                   v
                       +------------------------+
                       |      SQLite 3 DB       |
                       +-----------+------------+
                                   ^
                                   | SQLite Access
                       +-----------+------------+
                       |  Python Data Engine    |
                       | (Scheduler + Worker)   |
                       +----+--------------+----+
                            |              |
           Batch Poll Quotes|             | Send WeChat
                            v              v
               +----------------+ +-------------------+
               |  Tencent/Sina  | | WeChat Channel    |
               | Realtime API   | | (wechatpy)        |
               +----------------+ +-------------------+

```

---

## 2. 关键点识别与设计权衡 (Design Trade-offs)

### 关键点 1：Python 引擎与 SQLite 多进程并发写冲突

* **挑战**：Python 后台引擎高频更新/查询数据库，Next.js API 或 FastAPI 同时有写操作时，SQLite 容易发生 `database is locked` 错误。
* **设计权衡**：
* *方案 A*：完全依赖 SQLite 默认配置，读写时实时加锁。缺点：高频轮询下崩溃率极高。
* *方案 B*：采用 **SQLite WAL Mode (Write-Ahead Logging)** + **内存状态缓存（In-Memory State）**。盘中所有行情计算和频控拦截全部走内存，仅在必要时（如触发告警、更新股票状态）写入数据库。


* **决策**：**采用方案 B**。开启 SQLite WAL 模式，数据库只保存配置与持久化日志；实时行情和频控状态存放在 Python 后台进程的内存单例（Memory Engine）中。

### 关键点 2：实时计算性能与“前复权”数据基准处理

* **挑战**：如果每次 Tick/分钟轮询都重新计算过去 $N$ 天的前复权高低点，API 延迟和 CPU 消耗将不可接受；若不使用前复权，除权除息日会导致股价暴跌引发虚假买点。
* **设计权衡**：
* *方案 A*：每次实时 Tick 触发时，拉取 $N$ 天日线计算 $High_{max}$ 与 $Low_{min}$。缺点：耗时长，容易导致 HTTP 接口被限制。
* *方案 B*：**日盘前/盘后离线预计算 (Pre-computation)** + **今日复权对齐**。


* **决策**：**采用方案 B**。
1. 前复权（Forward Adjustment）的物理含义是：**以今日（T日）的价格为基准，将历史价格按除权因子缩放**。因此，T 日盘中的真实实时价格 $P$，可以直接与基于 T-1 日前复权的历史高低点进行对比。
2. 每日 **08:30** 运行定时任务，通过 Tushare/BaoStock 拉取最新前复权日线，计算过去 $N$ 天（不含当日）的 $Low_{min}$ 和 $High_{max}$ 存入 `daily_baselines` 表，并载入内存。盘中轮询只需做 $O(1)$ 的数值比较。



### 关键点 3：第三方行情 API 频控与批量轮询效率

* **挑战**：自选股池可能扩展到数十甚至上百只，若单只轮询会超出第三方 HTTP 限制，且响应延迟高。
* **设计权衡**：
* *方案 A*：并行单株并发请求。缺点：极易触发 IP 封禁。
* *方案 B*：利用新浪/腾讯 API 的 **拼接批量请求** 特性（如 `qt.gtimg.cn/q=sh600519,sz000001,sz300750`），单次 HTTP 获取全部自选股最新行情。


* **决策**：**采用方案 B**。支持将自选股打包成 50 只/批的 URL 批量拉取，轮询间隔设为 3~5 秒，最大化保护 API 稳定性。

---

## 3. 关键点详细设计决策

### 3.1 数据库 Schema 设计 (SQLite3)

开启 WAL 模式命令：`PRAGMA journal_mode=WAL;`

```sql
-- 1. 自选股表
CREATE TABLE watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code VARCHAR(10) NOT NULL UNIQUE, -- 如 "sh600519"
    stock_name VARCHAR(50) NOT NULL,
    status VARCHAR(10) DEFAULT 'NORMAL',     -- 'NORMAL'(正常), 'HALT'(停牌), 'DELISTED'(退市)
    custom_n INTEGER DEFAULT NULL,           -- 单股覆盖计算周期 N
    custom_x REAL DEFAULT NULL,              -- 单股覆盖买入系数 X
    custom_y REAL DEFAULT NULL,              -- 单股覆盖卖出系数 Y
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. 全局策略配置表 (仅单条记录)
CREATE TABLE strategy_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    global_n INTEGER DEFAULT 60,
    global_x REAL DEFAULT 1.10,
    global_y REAL DEFAULT 0.90,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. 每日策略计算基准表 (离线计算产物)
CREATE TABLE daily_baselines (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,               -- 交易日 YYYY-MM-DD
    low_min REAL NOT NULL,                  -- N日最低点
    high_max REAL NOT NULL,                 -- N日最高点
    actual_n INTEGER NOT NULL,              -- 实际有效天数 (处理次新股 < N)
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

-- 4. 日行情快照表 (每日OHLCV与换手率)
CREATE TABLE daily_market_snapshots (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,                 -- 交易日 YYYY-MM-DD
    open_price REAL NOT NULL,                 -- 开盘价
    high_price REAL NOT NULL,                 -- 最高价
    low_price REAL NOT NULL,                  -- 最低价
    close_price REAL NOT NULL,                -- 收盘价
    volume REAL NOT NULL,                     -- 成交量 (按数据源原始单位)
    turnover_rate REAL,                       -- 换手率(%), 个别数据源可能为空
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

-- 5. 微信通知发送与信号日志表 (兼顾频控持久化)
CREATE TABLE alert_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    signal_type VARCHAR(5) NOT NULL,         -- 'BUY' 或 'SELL'
    trigger_price REAL NOT NULL,             -- 触发时的实时价格 P
    baseline_price REAL NOT NULL,            -- 对应的 Low_min 或 High_max
    used_coeff REAL NOT NULL,                -- 使用的 X 或 Y 赋值
        sent_status VARCHAR(10) NOT NULL,        -- 'SUCCESS', 'FAILED'
    sent_time DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_freq_limit ON alert_logs(stock_code, trade_date, signal_type);

```

### 3.1.1 微信通知通道实现约定 (wechatpy)

* 使用 `wechatpy` 作为微信通知 SDK，通过企业微信应用消息或服务号模板消息发送告警。
* 推荐封装独立 `WeChatNotifier` 组件，统一处理 token 管理、重试、错误码解析与发送日志。
* 建议环境变量：
    * `WECHAT_CORP_ID`
    * `WECHAT_AGENT_ID`
    * `WECHAT_SECRET`
    * `WECHAT_TO_USER`（支持多用户）
* 发送成功/失败均写入 `alert_logs`，并保留响应码用于问题排查。

### 3.2 策略引擎与监控流水线 (Python Data Engine)

#### 阶段 A：每日盘前初始化 (Daily Baseline Job) - 08:30 运行

1. 读取 `watchlist` 中状态为 `NORMAL` 的所有股票。
2. 确定每只股票的计算周期 $N_{effective} = \text{custom\_n} \lor \text{global\_n}$。
3. 调用数据源拉取最近 $N_{effective} + 5$ 天的前复权（QFQ）日线数据。
4. **次新股逻辑**：若历史日线天数 $K < N_{effective}$，令 $N_{actual} = K$；若 $K=0$，标记异常。
5. 取剔除今日（T日）后的前 $N_{actual}$ 个交易日的 `low` 最小值 $Low_{min}$ 和 `high` 最大值 $High_{max}$。
6. 写入 `daily_baselines` 并更新至 Python **内存缓存字典** `BaselineCache`。

#### 阶段 B：盘中实时计算轮询 (Real-time Engine) - 09:30-11:30, 13:00-15:00

1. **时间过滤器**：判断当前时间是否在交易时段，非交易时间直接 Sleep，绝不触发告警。
2. **批量拉取行情**：拼接自选股代码批量访问腾讯 API（`[http://qt.gtimg.cn/q=sh600519,sz000001](http://qt.gtimg.cn/q=sh600519,sz000001)`）。
3. **停牌检测**：解析返回数据，若成交量/最新价为空或标识停牌，动态更新 `watchlist.status = 'HALT'`，跳过计算。
4. **信号匹配**：
* 读取内存中的系数 $X, Y$ 和基准值 $Low_{min}, High_{max}$。
* 计算买入门槛：$P_{buy\_limit} = Low_{min} \times X$
* 计算卖出门槛：$P_{sell\_limit} = High_{max} \times Y$
* 触发判断：
* **买点信号**：$P \le P_{buy\_limit}$
* **卖点信号**：$P \ge P_{sell\_limit}$





#### 阶段 C：收盘后日行情快照落库 (Daily Snapshot Job) - 15:30 运行

1. 读取 `watchlist` 中状态为 `NORMAL` 的股票列表。
2. 通过日线接口批量拉取当日 OHLCV + 换手率字段。
3. 对每只股票执行 UPSERT（`INSERT ... ON CONFLICT(stock_code, trade_date) DO UPDATE`）写入 `daily_market_snapshots`。
4. 若当日为非交易日或单只股票缺失数据，则记录 warning 日志并跳过，不阻塞其余股票写入。

### 3.3 告警防骚扰与频控决策树 (Notification System)

为避免价格在临界点反复震荡触发重复微信通知，采用 **内存 Set 校验 + SQLite 唯一索引** 结合的双重频控保证：

```
[触发策略条件: P <= Low_min * X]
               │
               ▼
   [检查交易时间: 09:30-15:00?] ────── (否) ───► [丢弃信号]
               │ (是)
               ▼
[检查内存: Set.contains(stock_code + trade_date + signal_type)?]
               │ (是: 今日已发过)
               ├───► [丢弃信号, 忽略]
               │ (否)
               ▼
  [尝试插入 SQLite alert_logs 表]
  (利用 UNIQUE(stock_code, trade_date, signal_type))
               │
      ┌────────┴────────┐
  (写入成功)        (写入失败: 触发约束)
      │                 │
      ▼                 ▼
[调用 wechatpy 下发微信通知]  [丢弃信号]
      │
      ▼
[更新内存防重 Set]

```

---

## 4. 边界异常处理与容错机制

| 异常场景 | 触发条件 | 解决方案/处理规则 |
| --- | --- | --- |
| **除权除息** | 股票在 T 日进行分红送股 | 08:30 的盘前任务采用**最新前复权**拉取历史高低点，计算出来的 $Low_{min}$ 和 $High_{max}$ 会自动按今日复权比例下调，与今日盘中未复权的实时价格 $P$ 同频，可直接比较。 |
| **新股/次新股** | 上市不足 $N$ 个交易日 | 实际历史天数 $K < N$ 时，直接以 $K$ 天计算高低点，并在 Next.js 前端列表展示角标“上市不足N天 ($K/N$)”。 |
| **网络超时/熔断** | 腾讯/新浪 API 请求超时 | 1. 采用 Exponential Backoff（指数退避）重试 3 次。<br>

<br>2. 连续 5 次失败后触发系统内部警报，并在前端展示“行情延迟”状态标志。 |
| **停牌/退市处理** | 盘中无法获取 Tick 或标识为停牌 | 自动更新 DB 状态为 `HALT`，监控引擎暂停处理该股票，直至盘前任务重新检测到复牌后恢复。 |
| **涨跌停板** | 触及信号时处于涨停/跌停 | V1.0 正常发送微信通知，通知文案末尾附加提示：`"（注：该股当前可能处于涨跌停状态，请注意流动性风险）"`。 |
