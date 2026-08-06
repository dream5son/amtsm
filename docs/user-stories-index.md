# 用户故事拆分总览（按场景）

本目录基于 PRD 与设计文档，将 V1.0 拆分为“一个场景一个文档”的用户故事集合。

## 文档清单
- 01 搜索并添加自选股：`user-story-01-search-and-add-watchlist.md`
- 02 移除自选股：`user-story-02-remove-watchlist.md`
- 03 自选列表展示与停牌状态同步：`user-story-03-watchlist-display-and-status.md`
- 04 配置策略参数（全局与单股覆盖）：`user-story-04-configure-strategy-parameters.md`
- 05 盘前基准值预计算（前复权）：`user-story-05-daily-baseline-precompute.md`
- 06 交易时段内实时轮询与信号判定：`user-story-06-intraday-polling-and-time-window.md`
- 07 配置微信发送通道（发件人、登录、收件人）：`user-story-07-configure-wechat-channel.md`
- 08 触发买点信号并发送微信通知：`user-story-08-buy-signal-alert.md`
- 09 触发卖点信号并发送微信通知：`user-story-09-sell-signal-alert.md`
- 10 告警频控与勿扰控制：`user-story-10-alert-frequency-and-dnd.md`
- 11 收盘后日行情快照落库：`user-story-11-daily-snapshot-job.md`
- 12 异常与容错处理：`user-story-12-exception-and-resilience.md`

## 说明
- 每个文档包含：场景目标、用户故事、前置条件、主流程、业务规则、验收标准（Given-When-Then）、非功能要求、关联数据。
- 可直接用于需求评审、研发任务拆分和测试用例编写。

## 界面改版说明（2026-08-04）
- V1.0 交互入口已调整为“单主界面工作台”。
- 搜索能力为辅助功能，内嵌在主界面内，不再作为独立功能页。
- 自选列表固定展示在主界面核心区域，支持就地查看与移除。
- 策略参数改为通过主界面弹框编辑与保存，不再依赖独立策略页面。
