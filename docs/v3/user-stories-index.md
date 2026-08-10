# 用户故事拆分总览（V3.0）

本目录基于 `docs/prd-v3.md`，将 V3.0 拆分为“一个场景一个文档”的用户故事集合。技术方案见 `docs/v3/design.md`。

**拆分原则**：每一个用户故事都必须能在主界面（自选列表工作台）或弹层/抽屉中通过 UI 操作与结果展示完成验收；纯后台无界面能力并入相关 UI 故事的业务规则。

**交付优先级（与 design §12 / PRD §7 对齐）**：先做回测 Buy & Hold 对比（量化现状），再做 `tier` 与双层仓位，最后做底仓信号与分层归因回测。

## 文档清单

| 编号 | 场景 | 文档 | 核心 UI 落点 | 建议优先级 |
| --- | --- | --- | --- | --- |
| 01 | 回测详情展示策略 vs 买入持有 | `user-story-01-backtest-vs-hold.md` | 回测详情新增对比指标与双净值曲线 | P0 |
| 02 | 列表/详情体现相对持有的结果摘要 | `user-story-02-alpha-vs-hold-summary.md` | 回测详情指标卡；可选列表角标 | P0 |
| 03 | 标记自选股策略类型（tier） | `user-story-03-mark-stock-tier.md` | 行内标签 + 编辑弹层 | P1 |
| 04 | 列表展示价值标签与路由提示 | `user-story-04-tier-display-routing-hint.md` | 列表标签、空态说明 | P1 |
| 05 | 配置底仓/波段比例与底仓参数 | `user-story-05-configure-core-params.md` | 策略参数弹框新增「底仓」分区 | P1 |
| 06 | 持仓分栏展示底仓与波段仓 | `user-story-06-core-satellite-position-display.md` | 持仓列簇分栏 | P1 |
| 07 | 登记买卖的分层分配与确认 | `user-story-07-layered-buy-sell.md` | 买入/减持表单分配预览与二次确认 | P1 |
| 08 | 底仓信号展示与人工复核 | `user-story-08-core-signals-and-review.md` | 信号列 + 通知 + 待复核入口 | P2 |
| 09 | 波段仓风控与文案隔离 | `user-story-09-satellite-risk-isolation.md` | 止损/止盈标识标明「仅波段仓」 | P2 |
| 10 | 双层策略回测与收益归因 | `user-story-10-layered-backtest-attribution.md` | 回测详情归因面板 | P2 |
| 11 | 价值策略相关异常与边界提示 | `user-story-11-core-satellite-edge-ui.md` | 降级拒绝、无对比、代理规则披露等 | P2 |

## 与 V1.0 / V2.0 的关系

- V1/V2 自选、监控、持仓登记、三级止损、回测任务与 K 线详情等能力沿用，不在本目录重复拆分。
- V3 默认不改变 `TACTICAL`（及未改标签的存量股）的 V2 行为。
- 回测圆环（V2 故事 09）第一版可不改语义；相对持有的结论以详情页（本目录故事 01/02/10）为主。

## 跨故事约定

1. **`tier` 枚举**：`TACTICAL`（交易型，V2 逻辑）/ `CORE_VALUE`（价值底仓+波段）。新股默认 `TACTICAL`。
2. **底仓类信号不自动下单、不自动改仓**：`CORE_BUILD` / `REVIEW_FUNDAMENTAL` / `CORE_TRIM` 仅提醒；改仓走用户确认后的登记买卖。
3. **`CORE_VALUE` 下**现有 `STOP_LOSS` / `TAKE_PROFIT` / `PARTIAL_TP` / `ADDON` **只作用于波段仓**；文案必须写明。
4. **Buy & Hold 起点**：策略第一次建仓日的成交价，持有至回测结束；策略全程未建仓则不展示 alpha 对比（故事 11）。
5. **免责**：回测与分层归因相关弹层保留「历史回测仅供参考，不构成投资建议」；若使用价格代理规则建仓/减仓，须标注非真实估值。

## 说明

- 每个文档包含：场景目标、用户故事、前置条件、主流程、业务规则、验收标准（Given-When-Then）、非功能要求、关联数据、UI 体现要点。
- 可直接用于需求评审、研发任务拆分和测试用例编写。
