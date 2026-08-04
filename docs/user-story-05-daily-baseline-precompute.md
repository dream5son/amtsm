# 用户故事 05：盘前基准值预计算（前复权）

## 场景目标
系统在交易日前（08:30）预计算每只股票的 N 日高低基准，盘中仅做 O(1) 阈值比较。

## 用户故事
作为一名依赖实时告警的投资者，
我希望系统在开盘前完成基准值准备，
以便盘中能稳定、快速地判断买卖信号。

## 参与角色
- 定时任务（APScheduler）
- 数据源接口（Tushare/BaoStock 等）
- 行情计算引擎
- SQLite 数据库

## 前置条件
- `watchlist` 中存在状态为 `NORMAL` 的股票。
- 第三方日线接口可用并支持前复权数据。

## 触发事件
- 每个交易日 08:30 触发盘前任务。

## 主流程
1. 读取全部正常股票及其生效参数 N。
2. 批量拉取前复权历史日线数据。
3. 对每只股票剔除当日后计算 `Low_min` 与 `High_max`。
4. 若历史数据不足 N 天，则以实际 K 天计算并记录 `actual_n`。
5. 写入 `daily_baselines` 并同步到内存 `BaselineCache`。

## 业务规则
- 必须使用前复权数据。
- N 日范围不含当日。
- 新股数据不足时采用 K 天降级策略，K=0 时记录异常。

## 验收标准（Given-When-Then）
- Given 历史数据充足，When 任务执行，Then `daily_baselines` 生成当日基准记录。
- Given 股票仅有 K<N 天数据，When 计算，Then `actual_n` 应为 K 且计算成功。
- Given 某股票无可用历史数据，When 任务执行，Then 该股票标记异常并不中断其他股票处理。

## 非功能要求
- 任务应支持失败重试与告警。
- 计算过程应尽量批量化，降低外部 API 调用次数。

## 关联数据
- 表：`watchlist`、`daily_baselines`
- 字段：`low_min`、`high_max`、`actual_n`、`trade_date`
