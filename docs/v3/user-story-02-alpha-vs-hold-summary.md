# 用户故事 02：相对持有结果摘要（alpha_vs_hold）

## 场景目标

把「策略相对买入持有好多少」收敛成可扫描的摘要指标，支持用多只典型价值股验证方案是否成立。

## 用户故事

作为一名需要快速比较多轮回测的投资者，
我希望直接看到相对买入持有的超额（或跑输）幅度与最大落后幅度，
以便不用盯着整条曲线也能判断策略是否拖累收益。

## UI 体现要点

- 回测详情指标卡新增：
  - `alpha_vs_hold`（策略年化 − Hold 年化），正负用颜色区分
  - `underwater_vs_hold`（相对 Hold 最大跑输幅度）及持续天数（若有）
- （可选加分）列表回测结果旁增加「优于持有 / 弱于持有」小标记；第一版可只做详情。

## 参与角色

- 用户
- 回测详情 / 列表摘要
- 回测指标计算

## 前置条件

- 故事 01 的 Hold 对比数据已产出。

## 触发事件

- 打开回测详情；或列表展示最近回测摘要时。

## 主流程

1. 回测完成或懒计算 Hold 后写入 `alpha_vs_hold` 等字段。
2. 详情指标卡读取并展示。
3. 若无 Hold 起点，显示「—」并引导见故事 11。

## 业务规则

- `alpha_vs_hold` 使用与详情一致的年化口径（与 `design.md` 一致）。
- `underwater_vs_hold = min(strategy_nav / hold_nav - 1)`（Hold 有效区段内）。
- 样本过少时仍展示，但沿用 V2「仅供参考」提示。

## 验收标准（Given-When-Then）

- Given 回测有 Hold 对比，When 打开详情，Then 可见 `alpha_vs_hold` 与 `underwater_vs_hold`。
- Given 策略年化高于 Hold，When 查看摘要，Then `alpha_vs_hold` 为正并有正向视觉反馈。
- Given 策略未建仓，When 打开详情，Then 上述指标为空或「无对比」，不显示误导性 0。

## 非功能要求

- 指标计算有单测；与曲线数据一致。

## 关联数据

- `backtest_jobs.alpha_vs_hold`、`underwater_vs_hold`、`underwater_vs_hold_days`
