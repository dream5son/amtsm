# 系统设计文档：A股交易信号监控告警系统 (V3.0)

本文档基于 `docs/prd-v3.md` 与 `docs/v3/` 用户故事全集，在 V2.0 设计（`docs/v2/design.md`）之上给出 **V3.0 整体技术方案**。目标是把策略从「纯技术择时、满仓进出」升级为「价值选股 + 底仓/波段双层仓位」，并用 Buy & Hold 基准证明「适当高抛低吸」是否真正增强收益。

**技术栈沿用 V2.0**：Python 3 + FastAPI + APScheduler + SQLite3（WAL）+ Next.js + `klinecharts`。V3.0 **不强制**接入基本面数据源；第一版以人工 `tier` 白名单打通路由，自动化估值/财务指标列为后续增强。

---

## 1. V3.0 设计目标与原则

### 1.1 产品升级一句话

| 维度 | V2.0 | V3.0 |
| --- | --- | --- |
| 核心问题 | 管住持仓盈亏 + 回测调参 | 区分「值得长期拿」与「交易型」；底仓穿越波动，波段仓做增强 |
| 仓位模型 | 单层仓位，止损/止盈即清仓 | **底仓（Core）+ 波段仓（Satellite）**；退出逻辑分层 |
| 选股 | 无筛选，自选统一规则 | `tier`：`CORE_VALUE` / `TACTICAL`，路由不同策略 |
| 信号 | BUY / STOP_LOSS / TAKE_PROFIT / PARTIAL_TP / ADDON | 沿用波段信号 + 新增 `CORE_BUILD` / `REVIEW_FUNDAMENTAL` / `CORE_TRIM` |
| 回测评估 | 回合胜率/盈亏比/回撤 | **必须对比 Buy & Hold**；分层收益归因；`alpha_vs_hold` 等 |

### 1.2 全局设计原则（贯穿全部模块）

1. **先分股票，再谈信号**：未标记或标记为 `TACTICAL` 的股票继续走 V2 纯技术满仓进出；仅 `CORE_VALUE` 启用底仓+波段双层逻辑。
2. **仓位分层，风控分层**：底仓不受日常百分比棘轮清仓；波段仓复用现有 `market_signal.risk` 规则核，作用范围仅限波段数量。
3. **规则单一实现、实盘与回测共用**：分层状态机与归因指标必须进共享领域包；禁止盘中一套、回测另一套。
4. **底仓退出以人工复核为主**：`REVIEW_FUNDAMENTAL` / 多数 `CORE_TRIM` / `CORE_BUILD` **只通知、不自动清仓**；用户确认后再改仓。
5. **一切新增判断可回测验证**：没有 Buy & Hold 对比与分层归因，不算 V3 回测完成。
6. **UI 仍锚定自选工作台**：标签、分栏持仓、复核入口、策略 vs 持有曲线均落在列表/弹层，不拆独立「价值股中心」。

### 1.3 本期明确非目标

- 不对接券商持仓/自动下单（与 V1/V2 一致）。
- 第一版不强制接入 PE/PB/ROE 等基本面 API；人工白名单即可。
- 不做多资产组合资金曲线、滑点与涨跌停成交模拟（延续 V2 回测非目标）。
- 不做基于 ATR 的参数自动推荐。
- 不推翻现有 N 日高低点 + 三级止损体系，仅缩小其作用范围为波段仓（`CORE_VALUE`）或保留满仓语义（`TACTICAL`）。

---

## 2. 系统整体架构（在 V2 上的演进）

V2 拓扑保持不变。V3 在同一进程内增加「选股路由」与「双层仓位态」：

```
                    +---------------------------+
                    |     Next.js Frontend      |
                    |  Watchlist Workbench      |
                    |  + Tier / Core-Sat UI     |
                    |  + BT vs Buy&Hold curves  |
                    +-------------+-------------+
                                  | REST
                                  v
                    +---------------------------+
                    |   Python FastAPI Server   |
                    |  Watchlist(tier) / Pos    |
                    |  (core|satellite) / BT    |
                    +-------------+-------------+
                                  | SQLite WAL
                                  v
                    +---------------------------+
                    |        SQLite 3 DB        |
                    |  V2 tables + tier +       |
                    |  position layers +        |
                    |  BT hold baseline fields  |
                    +-------------^-------------+
                                  |
                    +-------------+-------------+
                    |   Python Data Engine      |
                    |  Mode router by tier      |
                    |  Core evaluator (alerts)  |
                    |  Satellite = V2 risk核    |
                    |  Backtest: hold+layers    |
                    +----+-----------------+----+
                         |                 |
            Batch Quotes |                 | WeChat
                         v                 v
              Market APIs           wechatpy Channel
```

**进程内职责切分**：

| 组件 | V3 新增职责 |
| --- | --- |
| FastAPI | `tier` CRUD；底仓/波段数量读写；复核确认 API；回测结果扩展字段 |
| Data Engine（盘中） | 按 `tier` 路由；`TACTICAL`→V2；`CORE_VALUE`→双层评估；底仓信号只告警 |
| Backtest Worker | Buy & Hold 基准曲线；`alpha_vs_hold` 等；`CORE_VALUE` 路径模拟双层仓位与归因 |
| Shared Domain | 扩展 `market_signal/`：`tier` 路由、`core` 评估、分层回放；**不复制** V2 risk 公式 |

---

## 3. 领域模型与状态机（全局主线）

### 3.1 股票策略路由

```
              加入自选
                 │
                 v
         ┌───────────────┐
         │  标记 tier     │  人工 / 后续自动筛选
         └───────┬───────┘
                 │
     ┌───────────┴───────────┐
     v                       v
 TACTICAL                 CORE_VALUE
 (V2 满仓进出)            (底仓 + 波段)
```

- **默认值**：新加入自选默认为 `TACTICAL`（行为与 V2 完全一致，避免静默改变存量策略）。
- **复核周期**：产品建议季度/半年人工复核 `tier`；系统可提供「待复核」提醒（可选，不阻塞第一版）。

### 3.2 `CORE_VALUE` 持仓生命周期

```
         空仓（CORE_VALUE）
                 │
                 │ CORE_BUILD（分批建底仓，人工确认执行）
                 v
         ┌───────────────┐
         │ 有底仓         │◄── CORE_BUILD 继续加底仓档位
         │ 可同时有波段仓  │
         └───────┬───────┘
                 │
     ┌───────────┼────────────────┐
     v           v                v
  波段 ADDON   波段止损/止盈    REVIEW / CORE_TRIM
  (仅卫星仓)   PARTIAL_TP       (预警/建议减底仓，
               (仅卫星仓)         人工确认后改底仓)
```

- 波段仓清零 **不等于** 清仓：底仓仍在 → 模式仍为持仓（`HOLDING`），只是卫星数量为 0。
- 底仓与波段均清零 → 回到空仓监控（对 `CORE_VALUE` 仍可发 `CORE_BUILD`，而非仅技术 BUY——见 6.2）。

### 3.3 核心实体关系（相对 V2 增量）

```
Watchlist 1 ── 扩展 tier / tier_updated_at / tier_note
         1 ── 0..1 PositionState
                ├── qty_total（派生 = core_qty + satellite_qty）
                ├── core_qty / core_avg_cost
                ├── satellite_qty / satellite_avg_cost
                ├── satellite_highest / satellite_stop_price  （棘轮仅绑波段）
                └── core_build_stage（已建档位数，可选）
         1 ── * PositionLedger（增 layer: CORE|SATELLITE|UNSPLIT）
BacktestJob 扩展 hold_* 指标与归因字段
BacktestEquityPoint（可选）策略净值 + hold 净值序列，供对比曲线
GlobalStrategyConfig / Overrides 增 core_ratio、建仓档位等
```

**兼容策略**：`TACTICAL` 与存量数据可继续只维护 V2 的 `qty` / `avg_cost` / `highest_since_hold` / `stop_price`；或统一存为「全部计入 satellite、core=0」并在路由上仍按 V2 满仓语义处理。**推荐**：物理字段统一为 `core_*` + `satellite_*`，`TACTICAL` 写入时强制 `core_qty=0`，全部数量进 `satellite_*`，展示层对用户仍显示为「持仓」（不强调分层）。

### 3.4 信号类型扩展

| signal_type | 含义 | 适用 | 是否自动改仓 | 频控键 |
| --- | --- | --- | --- | --- |
| V2 既有类型 | 语义不变 | `TACTICAL`：整仓；`CORE_VALUE`：**仅波段仓** | 否（仅通知） | 日+股+类型 |
| `CORE_BUILD` | 按估值/价格档位建议建/加底仓 | `CORE_VALUE` 空仓或底仓未满 | 否 | 日+股+类型+档位 |
| `REVIEW_FUNDAMENTAL` | 大幅回撤或基本面预警，请人工复核 | `CORE_VALUE` 有底仓 | 否 | 日+股+类型（可按周去重） |
| `CORE_TRIM` | 估值极端泡沫，建议减底仓 | `CORE_VALUE` 有底仓 | 否 | 日+股+类型 |

`alert_logs.signal_type` 建议扩至 `VARCHAR(32)`。

---

## 4. 关键点识别与设计权衡

### 关键点 1：第一版如何定义「价值股」而无基本面数据

* **挑战**：PE/PB/ROE 数据源成本与接入周期不确定，但不能阻塞策略框架。
* **方案 A**：等数据源齐备再开发。缺点：立项与验证推迟。
* **方案 B**：**人工 `tier` 白名单**先跑通路由与双层仓位；预留 `fundamentals` 表/接口，后续自动打标只改写入 `tier` 的生产者。
* **决策：方案 B**（与 PRD §4/§7.1 一致）。

### 关键点 2：持仓表拆分方式（宽表 vs 子表）

* **挑战**：底仓/波段需要独立数量、成本；波段需要独立棘轮状态。
* **方案 A**：`positions` 宽表加列。优点：列表 join 简单。缺点：表变宽。
* **方案 B**：`position_layers(stock_code, layer)` 子表。优点：扩展灵活。缺点：聚合与事务更复杂。
* **决策：方案 A（宽表增量）** 作为 V3 第一版；若未来出现第三层再拆子表。`qty` / `avg_cost` 保留为派生或兼容列：`qty = core_qty + satellite_qty`；总成本按数量加权。

### 关键点 3：登记买卖时用户是否必须选「层」

* **挑战**：强制选层增加操作负担；自动分配又可能与用户意图不符。
* **方案 A**：每次买卖强制选择 CORE / SATELLITE。
* **方案 B**：`TACTICAL` 不选层；`CORE_VALUE` 默认规则——买入优先补齐目标底仓比例，超出部分记波段；卖出默认先减波段，波段为 0 再提示是否动底仓。
* **方案 C**：默认 B，表单提供「高级：指定层」覆盖。
* **决策：方案 C**。

### 关键点 4：回测 Buy & Hold 的起点如何定义

* **挑战**：「第一次建仓信号后持有到结束」对 `TACTICAL` 与 `CORE_VALUE` 起点不同。
* **方案 A**：区间首日收盘价买入。缺点：与策略入场时机不可比。
* **方案 B**：**以策略第一次实际建仓日（含 CORE_BUILD 或技术 BUY）的成交价**为 Hold 起点，持有到区间末日；若策略全程未建仓则不生成 Hold 对比（或标记 N/A）。
* **决策：方案 B**。指标与曲线均基于同一起点资金归一。

### 关键点 5：回测是否必须先上双层再上 Hold 对比

* **挑战**：PRD 强调 Hold 对比甚至可先于分层落地，用于量化「当前 V2 跑输多少」。
* **方案 A**：严格按双层完成后再做对比。
* **方案 B**：**两阶段**——Phase 0 先在现有满仓回测上加 Hold 基准与 `alpha_vs_hold`；Phase 1 再上 `CORE_VALUE` 双层回放与归因。
* **决策：方案 B**。用户故事与排期按此拆分。

### 关键点 6：底仓「估值分位建仓」无数据时如何触发 `CORE_BUILD`

* **挑战**：PRD 建议按 PE/PB 历史分位分批；无数据则档位无法自动算。
* **方案 A**：无数据则不做 `CORE_BUILD`，仅人工登记底仓。
* **方案 B**：用**价格相对 250 日均线/近 N 年价格分位**作为临时代理阈值（可配置），并在 UI 标注「代理规则，非估值分位」。
* **决策：方案 B 可选开关 + 方案 A 兜底**。全局配置 `core_build_mode = MANUAL | PRICE_PROXY | FUNDAMENTAL`；第一版默认 `MANUAL`（只提醒「可建底仓」或完全靠用户登记），`PRICE_PROXY` 作为加分项。

### 关键点 7：`REVIEW_FUNDAMENTAL` 的自动触发条件（无基本面时）

* **决策**：第一版用可量化代理——例如底仓浮亏超过阈值（如 25%/30%，可配置）或价格跌破年线且持续 N 日——触发复核提醒；文案明确「请人工核查基本面，系统不会自动卖出底仓」。有基本面数据后再叠加 ROE 恶化等规则。

---

## 5. 数据库 Schema 扩展（SQLite）

在 V2 表结构上增量演进；迁移须兼容已有库。

### 5.1 自选扩展 `tier`

```sql
ALTER TABLE watchlist ADD COLUMN tier VARCHAR(16) NOT NULL DEFAULT 'TACTICAL';
    -- TACTICAL | CORE_VALUE
ALTER TABLE watchlist ADD COLUMN tier_note TEXT;           -- 人工备注（护城河等）
ALTER TABLE watchlist ADD COLUMN tier_updated_at DATETIME;
```

可选待复核：

```sql
ALTER TABLE watchlist ADD COLUMN tier_review_due DATE;     -- 可选
```

### 5.2 持仓快照分层（`positions` 增列）

```sql
ALTER TABLE positions ADD COLUMN core_qty INTEGER NOT NULL DEFAULT 0;
ALTER TABLE positions ADD COLUMN core_avg_cost REAL;
ALTER TABLE positions ADD COLUMN satellite_qty INTEGER NOT NULL DEFAULT 0;
ALTER TABLE positions ADD COLUMN satellite_avg_cost REAL;
ALTER TABLE positions ADD COLUMN satellite_highest REAL;
ALTER TABLE positions ADD COLUMN satellite_stop_price REAL;
ALTER TABLE positions ADD COLUMN core_build_stage INTEGER NOT NULL DEFAULT 0;
```

迁移约定：

1. 存量行：`satellite_qty = qty`，`satellite_avg_cost = avg_cost`，`satellite_highest = highest_since_hold`，`satellite_stop_price = stop_price`，`core_qty = 0`。
2. 写路径维护：任何改仓后重算 `qty = core_qty + satellite_qty`，`avg_cost` 为两层数量加权（若一层为 0 则等于另一层成本）。
3. `TACTICAL`：禁止写入 `core_qty > 0`（API 校验）。

### 5.3 流水扩展 `layer`

```sql
ALTER TABLE position_ledgers ADD COLUMN layer VARCHAR(16) NOT NULL DEFAULT 'UNSPLIT';
    -- CORE | SATELLITE | UNSPLIT（历史行）
```

新写入必须带 `CORE` 或 `SATELLITE`（`TACTICAL` 统一写 `SATELLITE` 或 `UNSPLIT`——**推荐写 `SATELLITE`** 以简化聚合）。

### 5.4 策略配置扩展

**全局 / 单股覆盖** 增列示例：

```sql
-- 目标底仓占总仓比例，默认 0.6；波段 = 1 - core_ratio
ALTER TABLE strategy_config ADD COLUMN core_ratio REAL DEFAULT 0.60;
ALTER TABLE strategy_config ADD COLUMN core_build_mode VARCHAR(16) DEFAULT 'MANUAL';
ALTER TABLE strategy_config ADD COLUMN core_review_drawdown_pct REAL DEFAULT 0.25;
ALTER TABLE strategy_config ADD COLUMN core_trim_price_percentile REAL DEFAULT 0.95; -- 代理/未来估值
ALTER TABLE strategy_config ADD COLUMN core_build_ladder_json TEXT
    DEFAULT '[{"stage":1,"max_weight":0.30},{"stage":2,"max_weight":0.60},{"stage":3,"max_weight":1.0}]';
-- 波段仓可继续用既有 stop_loss / ladder；可选覆盖「波段专用」参数（加分项）
```

单股 `stock_strategy_overrides` 同步可空列，生效规则仍为 `override ?? global`。

### 5.5 回测任务扩展

```sql
ALTER TABLE backtest_jobs ADD COLUMN hold_total_return REAL;
ALTER TABLE backtest_jobs ADD COLUMN hold_annual_return REAL;
ALTER TABLE backtest_jobs ADD COLUMN alpha_vs_hold REAL;           -- 策略年化 - Hold 年化
ALTER TABLE backtest_jobs ADD COLUMN underwater_vs_hold REAL;      -- 相对 Hold 最大跑输幅度
ALTER TABLE backtest_jobs ADD COLUMN underwater_vs_hold_days INTEGER;
ALTER TABLE backtest_jobs ADD COLUMN core_hold_ratio REAL;         -- 底仓持有时间占比；TACTICAL/未分层可为 NULL
ALTER TABLE backtest_jobs ADD COLUMN core_pnl_contrib REAL;        -- 分层归因：底仓贡献
ALTER TABLE backtest_jobs ADD COLUMN satellite_pnl_contrib REAL;   -- 分层归因：波段贡献
ALTER TABLE backtest_jobs ADD COLUMN strategy_mode VARCHAR(16) DEFAULT 'TACTICAL';
    -- 任务快照：本次按 TACTICAL 或 CORE_VALUE 规则回放
```

对比曲线（推荐独立表，避免 JSON 过大）：

```sql
CREATE TABLE backtest_equity_curves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES backtest_jobs(id),
    trade_date DATE NOT NULL,
    strategy_nav REAL NOT NULL,     -- 策略净值，起点 1.0
    hold_nav REAL,                  -- Buy&Hold 净值；无 Hold 起点前可为 NULL
    UNIQUE(job_id, trade_date)
);
CREATE INDEX idx_bt_equity_job ON backtest_equity_curves(job_id, trade_date);
```

`backtest_trades` 可增：

```sql
ALTER TABLE backtest_trades ADD COLUMN layer VARCHAR(16);  -- CORE | SATELLITE | HOLD_MARK（可选）
ALTER TABLE backtest_trades ADD COLUMN exit_reason VARCHAR(32);
-- exit_reason 在 V2 基础上可扩展：CORE_TRIM_SIM | PERIOD_END | ...（模拟规则见 7.3）
```

---

## 6. 核心计算与引擎设计

### 6.1 有效参数与路由

```
params = resolve_params(stock_code)  # V2 merge + V3 core_* 字段
tier   = watchlist.tier
mode   = EMPTY | HOLDING  # 仍由总 qty 决定
branch = TACTICAL_ENGINE if tier == TACTICAL else CORE_SAT_ENGINE
```

### 6.2 盘中流水线（V3）

```
if tier == TACTICAL:
    # 完全复用 V2 6.3 流水线（满仓语义，数量在 satellite_*）
else:  # CORE_VALUE
    if core_qty == 0 and satellite_qty == 0:
        maybe CORE_BUILD (按 core_build_mode)
        # 可选：仍计算技术 BUY 作为「波段建仓」提示，但文案区分于底仓
    else:
        # —— 波段层 ——
        if satellite_qty > 0:
            update satellite highest/stop via market_signal.risk
            maybe STOP_LOSS / TAKE_PROFIT / PARTIAL_TP  # 仅针对 satellite
        if enable_addon and core still valid:
            maybe ADDON  # 加的是波段仓
        # —— 底仓层 ——
        maybe REVIEW_FUNDAMENTAL (drawdown / proxy rules)
        maybe CORE_TRIM (extreme premium proxy)
        maybe CORE_BUILD (未满目标 core_ratio)
```

**硬约束**：任何 `STOP_LOSS` / `TAKE_PROFIT` / `PARTIAL_TP` 在 `CORE_VALUE` 下 **不得** 暗示或自动导致 `core_qty` 变化；通知文案须写明「仅建议处理波段仓」。

### 6.3 底仓评估（规则核草案）

输入：`core_qty`, `core_avg_cost`, `price`, `params`, 可选 `valuation_percentile`。

| 信号 | 第一版触发（可配置） | 动作 |
| --- | --- | --- |
| `CORE_BUILD` | `MANUAL`：不自动；`PRICE_PROXY`：跌破档位阈值且 stage 未满 | 告警 + 建议数量 |
| `REVIEW_FUNDAMENTAL` | `(price - core_avg_cost)/core_avg_cost <= -core_review_drawdown_pct` | 告警，不改仓 |
| `CORE_TRIM` | 价格分位或乖离率代理超阈值 | 告警建议减仓比例，不改仓 |

全部纯函数化，供回测在「模拟人工始终确认/始终忽略」两种假设下做敏感性分析（第一版回测默认：**CORE_BUILD 按信号自动模拟建仓；REVIEW 不自动卖出；CORE_TRIM 按配置是否模拟减仓，默认不自动减**，并在报告中披露假设）。

### 6.4 持仓服务写路径

| API 能力 | V3 行为 |
| --- | --- |
| 设置 tier | 校验枚举；`CORE_VALUE→TACTICAL` 若 `core_qty>0` 需先处理底仓或拒绝并提示 |
| 登记买入 | 按关键点 3 分配 layer；更新对应成本与数量；刷新波段棘轮（加波段时） |
| 登记减持 | 默认先减 satellite；减 core 需显式确认 |
| 复核确认 | 用户确认 `CORE_TRIM`/`REVIEW` 后的减仓意图 → 走标准减持 API |
| 列表聚合 | 返回 tier、core/satellite 拆分、波段 stop、底仓复核状态 |

---

## 7. 回测系统设计（V3）

### 7.1 定位

```
         ┌── Buy & Hold 基准（同一入场起点）
回测 ────┼── 策略净值（TACTICAL 满仓 或 CORE_VALUE 双层）
         └── 归因：core_pnl_contrib / satellite_pnl_contrib
                    alpha_vs_hold / underwater_vs_hold / core_hold_ratio
```

### 7.2 Phase 0：在现有引擎上加 Hold 对比（优先）

对每个 SUCCESS job：

1. 找到策略**第一笔入场日** `T0` 与 `entry_price`。
2. 从 `T0` 到 `end_date`，持有 1 单位，按日收盘更新 `hold_nav`。
3. 策略侧用既有成交与 `PERIOD_END` 规则生成 `strategy_nav`（需显式落库，V2 若仅有指标无曲线则本阶段补齐）。
4. 计算：
   - `hold_total_return` / `hold_annual_return`
   - `alpha_vs_hold = strategy_annual_return - hold_annual_return`
   - `underwater_vs_hold` = `min(strategy_nav/hold_nav - 1)` 及持续交易日数

V2 的 `win_rate` 等回合指标**保留**，但详情页主对比升级为「策略 vs 持有」。

### 7.3 Phase 1：`CORE_VALUE` 双层回放

日线回放要点：

| 状态 | 动作 |
| --- | --- |
| 底仓未满 | 按 `core_build_mode` 模拟 `CORE_BUILD` 分批成交（记 `layer=CORE`） |
| 有底仓 | 底仓数量不变（除非开启模拟 `CORE_TRIM`）；不因卫星止损影响 core |
| 波段空仓 | 技术买点 → 买入卫星（资金/数量按 `1-core_ratio` 目标或剩余预算简化模型） |
| 波段持仓 | **只对卫星**跑 V2 三级止损；触发则平卫星，记卫星成交 |
| 区间结束 | 对仍持有的 core/satellite 分别或合并记 `PERIOD_END`（展示用，统计口径与 V2 对齐并文档化） |

简化假设（须在 UI 披露）：

- 单股、无组合现金约束的简化预算（例如总预算 1.0，core 目标 `core_ratio`）。
- `REVIEW_FUNDAMENTAL` 默认不触发模拟卖出。
- 与 V2 相同：无滑点、不计费、日线近似。

### 7.4 分层归因

在回测结束时：

- **core_pnl_contrib**：仅底仓数量变化与持有期间价格变动带来的盈亏（相对起点预算）。
- **satellite_pnl_contrib**：波段全部回合盈亏之和（含未平仓记账的展示项可单列）。
- **恒等式（允许舍入误差）**：`strategy_total_pnl ≈ core_pnl_contrib + satellite_pnl_contrib + cash_drag`。
- 若 `satellite_pnl_contrib` 长期为负，UI 提示「波段拖累，可考虑提高 core_ratio 或关闭波段」。

### 7.5 与前端约定

- 详情页：原 K 线买卖点 **保留**；新增 **策略净值 vs Hold 净值** 双曲线（可用 KLineChart 副图或独立折线组件）。
- 指标卡：突出 `alpha_vs_hold`、`underwater_vs_hold`；`CORE_VALUE` 任务另显归因与 `core_hold_ratio`。
- 列表圆环：可继续表示胜率，或增加「相对持有」小标记（加分项）；第一版不强制改圆环语义，避免破坏 V2 心智，详情页承担新指标。

---

## 8. 前端信息架构

主界面仍是自选列表工作台。

| 区域 | V3 内容 |
| --- | --- |
| 股票信息列 | `tier` 标签：`价值底仓` / `交易型`；可点开编辑 |
| 持仓列簇 | `CORE_VALUE`：底仓数量/成本、波段数量/成本/止损价分栏；`TACTICAL`：保持 V2 单行持仓 |
| 信号列 | 新增底仓类信号文案与「待复核」角标 |
| 行操作 | 「标记策略类型」、登记买卖（含层分配预览）、回测 |
| 回测详情 | 「策略 vs 买入持有」对比 +（分层任务）归因面板 |
| 策略配置 | 底仓比例、复核回撤阈值、建仓模式等分区 |

---

## 9. API 轮廓（逻辑分组）

| 分组 | 示例 | 说明 |
| --- | --- | --- |
| Watchlist | `PATCH /watchlist/{code}/tier` | 设置 tier/备注 |
| Positions | 买卖 API 增 `layer` / `allocation_preview` | 默认分配 + 覆盖 |
| Strategy | 全局/覆盖增 `core_*` 字段 | 与 V2 同一套 GET/PUT |
| Backtest | 详情 DTO 增 hold 指标；`GET .../equity-curve` | 策略+Hold 序列 |
| Alerts | 扩展类型与模板 | 底仓类通知 |

---

## 10. 通知模板与通道

通道沿用 V1/V2。新增模板要点：

- `CORE_BUILD`：建议档位、建议数量、当前 core 比例、是否代理规则。
- `REVIEW_FUNDAMENTAL`：强调**非自动卖出**、请核查基本面/是否降级 `tier`。
- `CORE_TRIM`：建议减仓比例、保留底仓下限提示。
- 波段类退出：文案前缀「【波段仓】」。

---

## 11. 边界与异常（横切）

| 场景 | 处理 |
| --- | --- |
| 降级 tier 仍有底仓 | API 拒绝或要求先转化/减持底仓 |
| 减持时波段不足 | 提示剩余将触及底仓，需二次确认 |
| 无 Hold 起点 | `alpha_vs_hold` 为空，UI 显示「策略未建仓，无对比」 |
| 代理规则误导 | UI/通知标明非真实估值 |
| 旧回测任务 | 无新字段则详情不展示对比，引导「重新回测」 |
| 存量持仓迁移 | 一律视为卫星/整仓，不自动拆底仓（避免臆测用户意图） |

---

## 12. 模块落地顺序建议（工程视角）

与 PRD §7 对齐，依赖从底向上：

1. **Schema：`tier` + 回测 Hold 字段/权益曲线表**
2. **回测 Phase 0：Buy & Hold 对比与 `alpha_vs_hold`（可先量化 V2 跑输）**
3. **前端：回测详情「策略 vs 持有」**
4. **`tier` API + 列表标签 + 路由骨架（TACTICAL 行为不变）**
5. **positions 分层字段迁移 + 买卖分配规则**
6. **盘中 CORE_VALUE 双层评估 + 新信号/通知**
7. **回测 Phase 1：双层回放 + 归因**
8. **策略配置 UI、复核流程、边界文案收口**
9. **（后续）基本面数据源 → 自动打标 / 真估值档位**

---

## 13. 测试策略（全局）

| 层级 | 重点 |
| --- | --- |
| 单元 | layer 分配、加权成本、core 信号触发、alpha/underwater 计算、归因恒等式 |
| 引擎 | tier 路由；CORE_VALUE 下止损不清底仓；TACTICAL 回归 V2 |
| 回测 | 同一 fixture：Hold 起点与策略首入场一致；分层任务 core 不被卫星止损清零 |
| API | tier 降级校验、减底仓二次确认、旧任务缺字段兼容 |
| 前端 | 标签、分栏、对比曲线、免责与假设披露 |

---

## 14. 与 V1/V2 设计的关系

- V1 监控链路、V2 持仓/棘轮/回测任务模型 **全部保留**。
- V3 是在 V2 上增加 **选股维（tier）** 与 **仓位结构维（core/satellite）**，并用 **Hold 基准** 校正回测评价，不是旁路第二套交易系统。
- `market_signal.risk` 继续作为波段（及 TACTICAL 整仓）唯一止损实现；底仓逻辑新建 `market_signal/core.py`（名称可调整），禁止把底仓退出塞进棘轮函数冒充。

---

## 15. 文档索引

| 文档 | 用途 |
| --- | --- |
| `docs/prd-v3.md` | 产品分析/方案权威来源 |
| `docs/v3/user-stories-index.md` | UI 可验收场景拆分 |
| `docs/v2/design.md` | V2 已落地技术方案 |
| **本文** | V3 整体技术方案与权衡 |

研发排期以本文领域边界为准，以用户故事为验收清单；优先交付 Hold 对比以验证立项假设，再交付双层仓位以免「感觉更好」却无法证伪。
