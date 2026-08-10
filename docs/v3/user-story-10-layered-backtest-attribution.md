# 用户故事 10：双层策略回测与收益归因

## 场景目标

对 `CORE_VALUE` 股票按底仓+波段规则回测，并在详情中展示底仓贡献、波段贡献与 `core_hold_ratio`，验证波段是否增强还是拖累。

## 用户故事

作为一名想验证「适当高抛低吸」是否划算的投资者，
我希望回测能按底仓/波段分开统计贡献，
以便发现波段长期为负时及时加大底仓比例或关掉波段。

## UI 体现要点

- 对价值股发起回测时，任务快照 `strategy_mode=CORE_VALUE`（可用当前 tier）。
- 详情在故事 01/02 基础上增加归因面板：
  - 底仓盈亏贡献
  - 波段盈亏贡献
  - `core_hold_ratio`（底仓持有时间占比）
- 若波段贡献为负，显示提示文案（可建议提高 `core_ratio`）。

## 参与角色

- 用户
- 回测配置/详情 UI
- 回测 Phase 1 引擎

## 前置条件

- 故事 01/02 已具备 Hold 对比；分层实盘模型已定义（故事 05–07）。

## 触发事件

- 用户对 `CORE_VALUE` 股票发起回测并打开详情。

## 主流程

1. 创建回测任务，写入策略模式与参数快照（含 `core_ratio` 等）。
2. Worker 按双层规则日线回放，写成交、权益曲线、归因字段。
3. 详情展示归因与 Hold 对比。

## 业务规则

- 卫星止损/止盈不得清掉模拟底仓。
- `REVIEW_FUNDAMENTAL` 默认不自动卖出；`CORE_TRIM` 是否模拟须在详情「回测假设」中披露（默认不自动减）。
- `CORE_BUILD` 按任务参数模拟分批建仓（与 `core_build_mode` 一致）。
- `TACTICAL` 回测不展示分层归因（或显示 N/A）。

## 验收标准（Given-When-Then）

- Given 价值股回测成功，When 打开详情，Then 可见底仓/波段贡献与 `core_hold_ratio`。
- Given 回放中波段触发止损，When 检查结果，Then 底仓模拟持仓仍在（除非假设启用 trim）。
- Given 交易型回测，When 打开详情，Then 不强制展示双层归因。

## 非功能要求

- 归因近似满足 `strategy_pnl ≈ core + satellite + cash_drag`（单测允许微小误差）。
- 回测假设在 UI 固定位置披露。

## 关联数据

- `backtest_jobs.core_pnl_contrib`、`satellite_pnl_contrib`、`core_hold_ratio`、`strategy_mode`
