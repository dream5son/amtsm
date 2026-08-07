# 系统设计文档：A股交易信号监控告警系统 (V2.0)

本文档基于 `docs/prd-v2.md` 与 `docs/v2/` 用户故事全集，在 V1.0 设计（`docs/v1/design.md`）之上给出 **V2.0 整体技术方案**。目标是把系统从「选股/择时监控」升级为「全生命周期持仓风控 + 参数决策支持」，设计视角按 **领域与横切能力** 组织，不以单个用户故事为章节主轴。

**技术栈沿用 V1.0**：Python 3 + FastAPI + APScheduler + SQLite3（WAL）+ Next.js。

---

## 1. V2.0 设计目标与原则

### 1.1 产品升级一句话

| 维度 | V1.0 | V2.0 |
| --- | --- | --- |
| 核心问题 | 发现买卖点 | 发现买卖点 + **管住持仓盈亏** + **用历史数据调参/选股** |
| 状态感知 | 只知「关注」 | 知「是否持仓、成本、数量、持仓期最高价」 |
| 信号语义 | BUY / SELL（技术高低点） | 空仓：BUY；持仓：STOP_LOSS / TAKE_PROFIT /（可选）PARTIAL_TP / ADDON |
| 决策依据 | 经验默认参数 | 实盘引擎 + **离线回测** 闭环 |

### 1.2 全局设计原则（贯穿全部模块）

1. **同一自选列表、双模式互斥**：`空仓监控` 与 `持仓风控` 共用 watchlist 工作台；同一股票同一时刻只跑一套主逻辑，避免「又买又止损」的矛盾主信号。
2. **规则单一实现、实盘与回测共用**：买点判定与三级止损体系必须抽成纯函数/领域服务，盘中引擎与回测引擎调用同一套代码，禁止两套公式漂移。
3. **棘轮（Ratchet）**：动态止损参考价 `StopPrice` 只允许向有利方向收紧（上移），不允许因加仓以外的行情波动而下移；加仓仅允许按新成本重算初始止损，且仍与既有 `StopPrice` 取 `max` 语义对齐产品规则。
4. **配置分层**：全局默认 + 单股覆盖，覆盖范围扩展到止盈止损参数与相关开关；回测「一键应用」默认只写单股覆盖。
5. **通知只提醒、不代下单**：持仓由用户手动登记；告警走微信；回测不产生实盘通知。
6. **UI 锚定主工作台**：持仓登记、流水、风控参数、回测触发与胜率圆环均落在自选列表及其弹层/抽屉，不拆独立「持仓 App」或「回测中心」为 V2 主路径。

### 1.3 本期明确非目标

- 不对接券商持仓/自动下单。
- 回测不做多股资金曲线、滑点与涨跌停成交模拟。
- 不做基于 ATR 的参数自动推荐（V3.0）。

---

## 2. 系统整体架构（在 V1 上的演进）

V1 的「前端 + FastAPI + Data Engine + SQLite」结构保持不变。V2 在同一进程拓扑上增加三类能力：

```
                    +---------------------------+
                    |     Next.js Frontend      |
                    |  Watchlist Workbench      |
                    |  + Position / Risk / BT UI|
                    +-------------+-------------+
                                  | REST
                                  v
                    +---------------------------+
                    |   Python FastAPI Server   |
                    |  Watchlist / Position /   |
                    |  Strategy / Backtest API  |
                    +-------------+-------------+
                                  | SQLite WAL
                                  v
                    +---------------------------+
                    |        SQLite 3 DB        |
                    |  V1 tables + positions /  |
                    |  ledgers / risk cfg /     |
                    |  daily_market_snapshots   |
                    |  (qfq cache) / BT jobs    |
                    +-------------^-------------+
                                  |
                    +-------------+-------------+
                    |   Python Data Engine      |
                    |  Baseline Job             |
                    |  Intraday Poller          |
                    |  + RiskControlEvaluator   |  <-- 与 BacktestSimulator 共用规则核
                    |  + PositionCorporateAdj   |
                    |  Backtest Worker (async)  |
                    +----+-----------------+----+
                         |                 |
            Batch Quotes |                 | WeChat
                         v                 v
              Realtime API          wechatpy Channel
              History API ----> daily_market_snapshots
```

**进程内职责切分**：

| 组件 | V2 新增职责 |
| --- | --- |
| FastAPI | 持仓登记/流水、风控参数 CRUD、回测任务创建与查询、列表聚合字段 |
| Data Engine（盘中） | 按持仓状态路由空仓/持仓逻辑；维护内存中的 `StopPrice` / `HighestSinceHold`；扩展信号类型与频控 |
| Backtest Worker | 异步跑历史回放；读写 `daily_market_snapshots`（前复权日线缓存）与回测结果表 |
| Shared Domain | `signal_rules`（买点）+ `risk_rules`（三级止损）纯计算库 |

---

## 3. 领域模型与状态机（全局主线）

### 3.1 股票在系统中的生命周期

```
              加入自选
                 │
                 v
         ┌───────────────┐
         │  空仓监控模式  │◄──────────────────────────┐
         │  (V1 买点为主) │                           │
         └───────┬───────┘                           │
                 │ 登记买入（建仓）                    │ 全部减持（清仓）
                 v                                   │
         ┌───────────────┐     部分减持      ┌───────┴───────┐
         │   持仓中       │───────────────►│  部分减持      │
         │ 止盈止损引擎开 │◄───────────────│  仍走持仓风控  │
         └───────────────┘   再加仓/继续持有└───────────────┘
```

- **空仓监控**：无成本、无 `StopPrice`；推送 BUY（及可选技术 SELL 辅助）。
- **持仓中 / 部分减持**：推送 STOP_LOSS / TAKE_PROFIT /（可选）PARTIAL_TP；默认抑制 BUY；可选 ADDON。
- **清仓**：清空持仓运行态字段，流水保留，模式回到空仓监控。

### 3.2 核心实体关系

```
Watchlist 1 ── 0..1 PositionState（当前持仓快照）
         1 ── * PositionLedger（买入/减持流水，清仓后仍保留）
         1 ── 0..1 StrategyOverride（单股 N/X/Y + 风控参数/开关）
         1 ── * BacktestJob（历史任务与结果摘要）
BacktestJob 1 ── * BacktestTrade（虚拟成交流水）
GlobalStrategyConfig 1（含 V1 参数 + V2 风控默认 + 开关）
DailyMarketSnapshot（复用 V1 表，升级为多年前复权日线缓存，服务回测/盘前/收盘落库）
```

**持仓快照 vs 流水**：快照服务实时风控与列表展示；流水服务复盘与已实现盈亏展示。任何改仓只通过「登记买入/减持」写流水并派生快照，禁止直接改历史流水来「修仓」。

### 3.3 信号类型扩展（全局枚举）

| signal_type | 含义 | 适用模式 | 频控键 |
| --- | --- | --- | --- |
| `BUY` | 空仓买入机会 | 空仓 | 日+股+类型 |
| `SELL` | 技术高位辅助（可选） | 空仓或持仓（若开启辅助） | 日+股+类型 |
| `STOP_LOSS` | 触及止损线且浮亏 | 持仓 | 日+股+类型；触发后当日停算该股风控 |
| `TAKE_PROFIT` | 触及止损线且浮盈（保本/移动） | 持仓 | 同上 |
| `PARTIAL_TP` | 浮盈跨阶梯档建议减仓 | 持仓且开关开 | 日+股+类型（按档或按类型，见 6.3） |
| `ADDON` | 持仓中加仓机会 | 持仓且开关开 | 日+股+类型 |

`alert_logs.signal_type` 字段长度需从 V1 的 5 字符扩展（建议 `VARCHAR(16)`），唯一索引仍为 `(stock_code, trade_date, signal_type)`。

---

## 4. 关键点识别与设计权衡

### 关键点 1：空仓逻辑与持仓逻辑如何共存且不互相干扰

* **挑战**：同一轮询批次里，若对持仓股仍推 BUY，或空仓股算止损，会造成错误决策与通知轰炸。
* **方案 A**：两套调度器、两张列表。缺点：UI/数据分裂，违背 PRD「同一工作台」。
* **方案 B**：**单调度器 + 模式路由**。每只股票根据 `position_qty > 0` 进入 `EMPTY` 或 `HOLDING` 分支。
* **决策：方案 B**。列表聚合 API 一次返回双模式所需字段；引擎内用显式 `mode` 分支，规则核函数入参区分。

### 关键点 2：实盘与回测规则一致性

* **挑战**：回测若另写一套「简化止损」，会出现「回测赚钱、实盘不触发」。
* **方案 A**：回测复制粘贴公式。缺点：必漂移。
* **方案 B**：抽出 `RiskControlEngine.evaluate(bar_or_tick, state, params) -> (new_state, signal?)` 与 `EntrySignal.evaluate(...)`，实盘用实时价调用，回测用日线 OHLC（默认用收盘价判定触发，最高价更新 `HighestSinceHold`）调用。
* **决策：方案 B**。回测日线近似规则在文档中写死（见 7.3），避免实现歧义。

### 关键点 3：动态止损状态放内存还是库

* **挑战**：`HighestSinceHold`、`StopPrice` 盘中高频更新；进程重启不能丢。
* **方案 A**：仅内存。缺点：重启丢棘轮进度。
* **方案 B**：每次创新高/止损线上移都写库。缺点：SQLite 写放大。
* **方案 C**：**库为权威快照 + 内存热缓存**；盘中变更先改内存，按「创新高 / 止损线上移 / 触发信号 / 用户改仓」落库；引擎启动时从 `positions` 灌入内存。
* **决策：方案 C**（延续 V1「计算走内存、必要持久化」）。

### 关键点 4：多年历史行情与回测性能

* **挑战**：每次回测拉 3–5 年前复权日线，易触第三方限流；多股/多参数网格更重。另：V1 已有 `daily_market_snapshots`，若再引入独立 `daily_bars` 易出现双轨价格基准。
* **方案 A**：每次回测直打第三方。缺点：慢且不稳。
* **方案 B**：新建独立 `daily_bars`（前复权），与现有快照表并存。缺点：两套 OHLCV、语义易漂移。
* **方案 C**：**复用并升级 `daily_market_snapshots` 为唯一日线缓存**：写入统一改为前复权；按股票增量补齐缺失区间；回测只读该表；后台可预热自选股近 5 年。
* **决策：方案 C**。不新增日线表；V1 收盘/盘中快照写入路径改为与回测同一前复权契约（见 5.4）。

### 关键点 5：回测任务同步还是异步

* **挑战**：单股 3 年逐日回放 + 多参数对比可达数秒到数十秒，同步 HTTP 会拖死列表交互。
* **方案 A**：请求内同步算完。缺点：超时、阻塞。
* **方案 B**：**任务表 + 引擎侧 Worker**（APScheduler 间隔拉取 PENDING，或内存队列）；前端轮询任务状态；完成后刷新首列胜率圆环。
* **决策：方案 B**。单用户本地场景无需分布式队列；SQLite 任务表足够。

### 关键点 6：除权除息对持仓成本与止损线

* **挑战**：实时价已是「今日基准」，若成本/最高价未按同一前复权因子调整，棘轮与触发会整体错位。
* **决策**：盘前任务在拉最新前复权日线时，检测复权因子变化（或对比昨收复权价跳变），对 `avg_cost`、`highest_since_hold`、`stop_price` **乘同一调整系数**；流水表保留原始成交价不改写（复盘展示历史成交），快照与风控字段必须对齐当日价格基准。

---

## 5. 数据库 Schema 扩展（SQLite）

在 V1 表结构上增量演进；迁移脚本需兼容已有库。开启 WAL 不变。

### 5.1 持仓快照 `positions`

```sql
CREATE TABLE positions (
    stock_code VARCHAR(10) PRIMARY KEY,
    qty INTEGER NOT NULL DEFAULT 0,              -- 当前持有数量；0 表示空仓
    avg_cost REAL,                              -- 加权成本；空仓 NULL
    highest_since_hold REAL,                    -- 持仓期间最高价；空仓 NULL
    stop_price REAL,                            -- 当前动态止损参考价；空仓 NULL
    position_status VARCHAR(20) NOT NULL
        DEFAULT 'EMPTY',                        -- EMPTY | HOLDING | PARTIAL
    opened_at DATETIME,                         -- 本轮建仓时间（清仓后清空）
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (stock_code) REFERENCES watchlist(stock_code)
);
```

> 也可用 `watchlist` 宽表扩展字段；独立 `positions` 更清晰，且清仓时不必污染 watchlist 主数据。**推荐独立表**。

### 5.2 持仓流水 `position_ledgers`

```sql
CREATE TABLE position_ledgers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code VARCHAR(10) NOT NULL,
    side VARCHAR(8) NOT NULL,                   -- BUY | SELL
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    trade_date DATE NOT NULL,                   -- 用户登记的成交日
    realized_pnl REAL,                          -- 减持时可选写入
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ledger_stock_time ON position_ledgers(stock_code, trade_date DESC, id DESC);
```

### 5.3 策略配置扩展

**全局 `strategy_config` 增列**（示例）：

```sql
ALTER TABLE strategy_config ADD COLUMN stop_loss_pct REAL DEFAULT 0.08;
ALTER TABLE strategy_config ADD COLUMN break_even_trigger_pct REAL DEFAULT 0.10;
ALTER TABLE strategy_config ADD COLUMN break_even_buffer_pct REAL DEFAULT 0.005;
ALTER TABLE strategy_config ADD COLUMN trailing_ladder_json TEXT
    DEFAULT '[{"min_pnl":0.10,"max_pnl":0.20,"drawdown":0.15},
              {"min_pnl":0.20,"max_pnl":0.50,"drawdown":0.10},
              {"min_pnl":0.50,"max_pnl":null,"drawdown":0.06}]';
ALTER TABLE strategy_config ADD COLUMN enable_partial_take_profit INTEGER DEFAULT 0;
ALTER TABLE strategy_config ADD COLUMN enable_addon_alert INTEGER DEFAULT 0;
ALTER TABLE strategy_config ADD COLUMN enable_tech_sell_while_holding INTEGER DEFAULT 0;
```

**单股覆盖**：在 `watchlist` 增同类可空列，或独立 `stock_strategy_overrides` 表（推荐独立表，避免 watchlist 无限变宽）：

```sql
CREATE TABLE stock_strategy_overrides (
    stock_code VARCHAR(10) PRIMARY KEY,
    custom_n INTEGER,
    custom_x REAL,
    custom_y REAL,
    stop_loss_pct REAL,
    break_even_trigger_pct REAL,
    break_even_buffer_pct REAL,
    trailing_ladder_json TEXT,
    enable_partial_take_profit INTEGER,         -- NULL=跟随全局
    enable_addon_alert INTEGER,
    enable_tech_sell_while_holding INTEGER,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

生效规则：字段级 `override ?? global`。

### 5.4 日线缓存：复用并升级 `daily_market_snapshots`

**决策**：不新建 `daily_bars`。V1 已有表结构（`stock_code + trade_date` 主键、OHLCV、换手率）直接作为 V2 唯一日线缓存，同时服务：

- 收盘/盘中快照落库（V1 原职责）
- 盘前 baseline 所需历史窗口
- 回测多年区间回放（V2 新职责）

**相对 V1 的契约变更（必须落地）**：

| 项 | V1 现状 | V2 统一契约 |
| --- | --- | --- |
| 复权 | 收盘任务经 `fetch_trade_day_bar` 写**不复权**价 | 所有写入统一为**前复权（qfq）** OHLCV，与实时监控高低点基准一致 |
| 覆盖 | 偏「上线后逐日累积」，窗口短 | 支持按股票**增量回填**缺失区间（默认可预热近 3–5 年） |
| 除权后历史行 | 未系统重刷 | 检测到复权基准变化时，对该股缓存区间做**前复权重刷/等价调整**，保证旧行与今日价同基准可比 |

表结构沿用 V1，仅按需增列（可选）：

```sql
-- 表名不变：daily_market_snapshots
-- 既有字段：open_price / high_price / low_price / close_price / volume / turnover_rate
ALTER TABLE daily_market_snapshots ADD COLUMN adj_factor REAL;  -- 可选，便于除权检测与重刷
-- 语义约定：OHLCV 一律为「以最新交易日为基准的前复权价」
```

**读写约定**：

1. **唯一写入口**：封装 `upsert_qfq_bars(stock_code, bars[])`，收盘任务、盘前补齐、回测前补洞、除权重刷均走此入口，禁止旁路写入不复权价。
2. **回测读路径**：只读本地 `daily_market_snapshots`；发现区间缺口则先补齐再跑；补齐失败则 job `FAILED`。
3. **迁移**：已存在的不复权历史行不可直接当回测数据用；升级时对该股触发一次前复权全量/窗口重刷，或清空后按需回填。
4. **盘中未收盘行**：若仍用实时行情 UPSERT「当日」行，须明确当日行为「盘中临时前复权近似」，收盘任务以日线接口最终覆盖；回测默认只用已收盘交易日，避免把半日 bar 当完整日线。

### 5.5 回测任务与流水

```sql
CREATE TABLE backtest_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code VARCHAR(10) NOT NULL,
    status VARCHAR(16) NOT NULL,                -- PENDING|RUNNING|SUCCESS|FAILED
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    params_json TEXT NOT NULL,                  -- 完整参数快照
    win_rate REAL,
    avg_win_loss_ratio REAL,
    max_drawdown REAL,
    trade_count INTEGER,
    total_return REAL,                          -- 加分项
    annual_return REAL,                         -- 加分项
    sample_insufficient INTEGER DEFAULT 0,      -- 交易次数过少标记
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME
);

CREATE TABLE backtest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES backtest_jobs(id),
    entry_date DATE NOT NULL,
    entry_price REAL NOT NULL,
    exit_date DATE NOT NULL,
    exit_price REAL NOT NULL,
    hold_days INTEGER NOT NULL,
    pnl_pct REAL NOT NULL,
    pnl_amount REAL,                            -- 按 1 手或单位仓位规范化
    exit_reason VARCHAR(32)                     -- STOP_LOSS|TAKE_PROFIT|...
);
CREATE INDEX idx_bt_trades_job ON backtest_trades(job_id);
```

列表首列圆环默认取：**该股「参数快照与当前生效参数一致」的最近 SUCCESS 任务**；不一致则展示该结果并打「过期」标记。

### 5.6 `alert_logs` 兼容改造

- 扩展 `signal_type` 长度与取值。
- `baseline_price` 复用为「触发时的 StopPrice 或高低点基准」；可新增可空列 `avg_cost`、`highest_since_hold` 便于通知模板与复盘（可选）。
- 保留 UNIQUE `(stock_code, trade_date, signal_type)`。

---

## 6. 核心计算与引擎设计

### 6.1 有效参数解析

```
resolve_params(stock_code) =
  merge(global_strategy_config, stock_strategy_overrides[stock_code])
```

输出统一结构：`{n, x, y, stop_loss_pct, break_even_trigger_pct, break_even_buffer_pct, trailing_ladder, enable_partial_tp, enable_addon, enable_tech_sell}`。

### 6.2 三级止损状态机（规则核）

输入：`avg_cost`, `highest_since_hold`, `stop_price`(prev), `qty`, `price`(当前价或回测用价), `params`。

每个评估周期：

1. **更新最高价**：`highest = max(highest_since_hold or price, price)`（回测日线：用当日 `high` 更新最高，用 `close` 做触发判断——见 7.3）。
2. **初始止损候选**：`stop0 = avg_cost * (1 - stop_loss_pct)`。
3. **保本候选**：若 `pnl_pct = (price - avg_cost)/avg_cost >= break_even_trigger_pct`，则  
   `stop1 = avg_cost * (1 + break_even_buffer_pct)`。
4. **阶梯移动止盈候选**：按 `pnl_pct` 匹配 ladder 档得 `drawdown_pct`；若处于可移动档（默认浮盈 ≥ 10%），  
   `stop2 = highest * (1 - drawdown_pct)`。
5. **棘轮合并**：`stop_price = max(prev_stop or 0, stop0, stop1?, stop2?)`（未触发保本/移动时不加对应候选）。
6. **触发**：若 `price <= stop_price`：  
   - `price < avg_cost` → `STOP_LOSS`  
   - else → `TAKE_PROFIT`
7. **分批止盈（可选）**：若开启且 `pnl_pct` 首次进入更高 ladder 档，发 `PARTIAL_TP`（不替代棘轮止损）。

加仓后：按加权成本重算 `avg_cost`，**不重置** `highest_since_hold`；以新成本重算 `stop0` 后再次棘轮 `max`；API 在确认页返回预览 `{new_avg_cost, new_stop_price}`。

减持：数量减少，`avg_cost` 不变；清仓则快照字段清空，`position_status=EMPTY`。

### 6.3 盘中流水线（扩展 V1 阶段 B）

对批量行情中的每只股票：

```
if qty == 0:
    eval BUY (V1)
    optional SELL (V1 tech)
else:
    update highest / stop_price (risk rules)
    if not already_fired_exit_today:
        maybe STOP_LOSS / TAKE_PROFIT
    if enable_partial_tp: maybe PARTIAL_TP
    if enable_addon: maybe ADDON (reuse buy threshold, different signal_type)
    if enable_tech_sell: maybe SELL as auxiliary (文案区分)
```

**频控**：沿用「内存 Set + DB UNIQUE」。对 `STOP_LOSS`/`TAKE_PROFIT`：**一旦当日成功记入退出类信号，当日对该股跳过后续风控评估**（用户应已收到离场指引）。`PARTIAL_TP` 建议按「档位」编码进 signal_type 或附加唯一键策略（若保持单一 `PARTIAL_TP`，则每日仅一次跨档提醒）。

### 6.4 盘前任务扩展（阶段 A）

在 V1 baseline 预计算之外增加：

1. 经统一写入口刷新自选股前复权日线至 `daily_market_snapshots`（至少保证 N 日基准；可顺带增量补齐回测常用窗口）。
2. 若检测到复权因子/昨收跳变：重刷该股快照缓存中受影响区间，并做 **持仓复权调整**（`avg_cost` / `highest_since_hold` / `stop_price`）。
3. 重载内存 `PositionCache` 与 `BaselineCache`（baseline 可直接基于快照表计算，减少重复拉第三方）。

### 6.5 持仓服务（API 侧写路径）

| API 能力 | 行为要点 |
| --- | --- |
| 登记买入 | 校验 qty/price；写 ledger；更新 position；预览接口可先不算写入 |
| 登记减持 | qty≤持仓；部分/清仓分支；写 realized_pnl |
| 流水查询 | 按股倒序；清仓后仍可查 |
| 列表聚合 | join 行情、position、最新 backtest 摘要、当前 stop_price |

写路径成功后通知引擎刷新该股内存状态（进程内事件或短轮询读库）。

---

## 7. 回测系统设计

### 7.1 定位

回测是 **选股与调参的决策支持层**，与持仓风控、策略配置形成闭环：

```
配置参数 ──► 实盘双模式引擎
    ▲              │
    │              │ 同一规则核
    │              ▼
一键应用 ◄── 回测任务 ◄── daily_market_snapshots（前复权）
                │
                ▼
         列表首列胜率圆环 / 详情对比
```

### 7.2 任务生命周期

`PENDING → RUNNING → SUCCESS|FAILED`

- 创建：单股（主路径）；可扩展同请求多 `params_json` 生成多个 job 便于对比。
- Worker：检查并增量补齐 `daily_market_snapshots` 目标区间 → 逐日模拟 → 写 `backtest_trades` + 汇总指标 → 更新 job。
- 失败：写入 `error_message`，前端圆环失败占位，不覆盖旧 SUCCESS 展示（除非产品选择覆盖；**推荐保留上次成功结果并提示本次失败**）。

### 7.3 日线回放约定（与实盘对齐的近似）

对每个交易日 `D`：

| 模拟状态 | 动作 |
| --- | --- |
| 空仓 | 若 `close <= Low_min(D)*X`（Low_min 基于 D 之前 N 日）→ 以 `close` 模拟买入 |
| 持仓 | 用当日 `high` 更新 `HighestSinceHold` 并重算 `StopPrice`；若 `low <= StopPrice` → 以 `min(open, StopPrice)` 或 `StopPrice` 作为卖出价（实现时选定一种并单测固定）；标记 exit_reason |

简化假设（PRD 非目标对齐）：满仓进出、无滑点、无涨跌停无法成交、不计手续费（保本缓冲仍按参数模拟）。

样本不足：`trade_count < 阈值`（建议默认 5，可配置）→ `sample_insufficient=1`，UI 强制提示。

### 7.4 指标计算

- 胜率 = 盈利笔数 / 总笔数  
- 平均盈亏比 = 平均盈利幅度 / 平均亏损幅度（绝对值）  
- 最大回撤 = 模拟净值曲线峰值到谷底最大跌幅（单位仓位累乘）  
- 累计/年化收益为加分项

### 7.5 与前端约定

- 操作区「回测」→ 配置区间/参数 → 异步任务。
- 第一列圆环 = `win_rate`；点击进详情（指标 + 流水 + 多 job 对比排序）。
- 「应用为单股策略」写 overrides，不改全局（除非显式勾选）；应用后圆环标记过期直至重跑。

---

## 8. 前端信息架构（工作台一体）

主界面仍是 **自选列表工作台**，V2 在列与操作上扩展，不新增一级导航也可完成核心闭环。

| 区域 | V2 内容 |
| --- | --- |
| 第一列 | 回测胜率圆环（空/进行中/成功/失败/样本不足/过期） |
| 行情与信号列 | 扩展止损/止盈/分批/加仓标识 |
| 持仓列簇 | 状态、数量、成本、浮动盈亏、止损参考价、距止损空间 |
| 行操作区 | 登记买入、登记减持、流水、回测、策略、移除（V1） |
| 全局入口 | 策略参数（含止盈止损分区与开关） |

弹层职责：买入/减持表单（含加仓预览）、流水抽屉、策略配置、回测配置与详情对比。回测相关弹层常驻免责声明。

---

## 9. API 轮廓（逻辑分组）

不必一次实现全部路径名，但边界应按领域切开：

| 分组 | 示例 | 说明 |
| --- | --- | --- |
| Positions | `POST /positions/{code}/buys` `POST .../sells` `GET .../ledgers` `GET .../preview-buy` | 改仓与预览 |
| Strategy | 扩展现有 strategy GET/PUT；overrides CRUD | 含风控字段 |
| Watchlist | 列表 DTO 扩展 position + stop + backtest summary | 一次聚合 |
| Backtest | `POST /backtests` `GET /backtests/{id}` `GET /backtests?stock=` `POST /backtests/{id}/apply` | 异步任务与应用参数 |
| Alerts | 沿用日志查询，扩展类型 | 可观测性 |

列表接口应避免 N+1：服务端 join/批量查最新 job 摘要。

---

## 10. 通知模板与通道

通道与重试沿用 V1 `WeChatNotifier`。模板按信号类型分支（文案见 PRD 3.6），并在元数据中支持附加：

- T+1：若 ledger 存在「当日买入」且触发退出类信号 → 附加当日买入不可卖出说明。
- 涨跌停：沿用 V1 风险提示。

列表信号文案与微信模板类型必须一致（同一 `signal_type` 驱动）。

---

## 11. 边界与异常（横切）

| 场景 | 处理 |
| --- | --- |
| 减持超量 | 前后端双校验，拒绝负持仓 |
| 加仓摊薄 | 确认前预览新成本与新止损价 |
| 除权除息 | 重刷该股 `daily_market_snapshots` 前复权区间；同步调整持仓快照字段；可选 toast |
| T+1 | 仅文案提示，不阻断信号计算 |
| 涨跌停 | 照常通知 + 流动性提示 |
| 同轮询创新高又跌破止损 | 先更新 highest/stop，再判断触发 |
| 回测数据缺失 | 先增量补齐 `daily_market_snapshots`；仍不足则 job FAILED |
| 旧库不复权脏数据 | 升级迁移时按股重刷或清空后回填，禁止混用未复权行做回测 |
| 回测样本少 | 结果可展示但强制「仅供参考」 |
| 参数变更未重跑 | 圆环过期角标 |
| SQLite 并发 | 延续 WAL；回测写任务与盘中写告警避免长事务 |

---

## 12. 模块落地顺序建议（工程视角，非故事编号）

按依赖从底向上，保证「规则核 → 持仓态 → 盘中路由 → 回测闭环」：

1. **Schema 迁移 + 参数模型扩展**（全局/单股风控字段）
2. **规则核纯函数与单测**（三级止损、棘轮、买点；实盘/回测共用）
3. **持仓服务与列表聚合**（登记买卖、流水、展示字段）
4. **盘中引擎模式路由 + 通知模板/频控扩展**
5. **`daily_market_snapshots` 前复权契约迁移 + 增量回填 + 盘前复权/持仓调整**
6. **回测 Worker + 任务 API**（只读升级后的快照表）
7. **前端工作台：持仓列/表单 → 风控配置 → 信号展示 → 回测圆环/详情/应用**
8. **边界提示与免责声明收口**

该顺序保证任何 UI 故事验收时，背后已是统一领域模型，而不是按故事堆出分叉逻辑。

---

## 13. 测试策略（全局）

| 层级 | 重点 |
| --- | --- |
| 单元 | 加权成本、棘轮、档位匹配、信号分类、指标汇总 |
| 引擎 | 空仓/持仓路由、退出后当日停算、开关矩阵（分批/加仓/技术卖） |
| 回测 | 同一 fixture 日线上，规则核与回测回放结果一致；样本不足标记 |
| API | 减持校验、加仓预览、apply 只写单股、任务状态机 |
| 前端 | 双模式列展示、圆环状态机、回测免责与过期角标 |

---

## 14. 与 V1 设计的关系

- V1 的 WAL、盘前 baseline 预计算、批量行情、告警频控、微信通道 **全部保留**。
- V2 是在「关注 → 信号」链路上插入 **持仓状态维**，并增加 **离线回测维**；不是旁路第二套系统。
- **`daily_market_snapshots` 表名与主键保留**，职责从「当日不复权归档」升级为「前复权日线唯一缓存」；V1 收盘/盘中写入路径须随契约一并改造，回测不得另起第二套日线表。

---

## 15. 文档索引

| 文档 | 用途 |
| --- | --- |
| `docs/prd-v2.md` | 产品需求权威来源 |
| `docs/v2/user-stories-index.md` | UI 可验收场景拆分 |
| `docs/v1/design.md` | V1 架构与已落地决策 |
| **本文** | V2 整体技术方案与权衡 |

研发排期与测试用例应以本文的领域边界为准，以用户故事为验收清单；避免按单个故事单独长出互不兼容的数据模型或第二套止损公式。
