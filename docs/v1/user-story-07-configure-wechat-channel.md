# 用户故事 07：配置微信发送通道（发件人、登录、收件人）

## 场景目标
在买卖点告警发送之前，完成微信通知通道的安装、发件人（企业微信应用）鉴权登录与收件人配置，使后续信号通知可稳定触达用户。

## 用户故事
作为一名依赖微信告警的投资者，
我希望能够配置微信发送通道的发件人凭证、完成登录鉴权，并指定收件人，
以便买点/卖点信号触发时系统能通过微信把提醒准确发送给我。

## 参与角色
- 用户（系统管理员/本人）
- 后端配置与通知组件（`WeChatNotifier` / wechatpy）
- 企业微信 API

## 前置条件
- 后端已按 wechatpy 文档安装依赖：`pip install wechatpy`（推荐 `pip install wechatpy[cryptography]`）。
- 参考文档：https://wechatpy.readthedocs.io/zh-cn/stable/install.html
- 用户已在企业微信管理后台创建可用应用，并具备 CorpID、AgentId、Secret。
- 收件人已关注/加入企业微信，且 UserID 已知。

## 触发事件
- 用户首次部署系统，或需要变更微信通道、发件人、收件人配置时。

## 主流程
1. 安装 wechatpy（若尚未安装），确认版本与 Python 环境兼容。
2. 配置发件人（企业微信应用）环境变量：
   - `WECHAT_CORP_ID`：企业 ID（CorpId）
   - `WECHAT_AGENT_ID`：应用 AgentId
   - `WECHAT_SECRET`：应用 Secret
3. 配置收件人：
   - `WECHAT_TO_USER`：企业微信 UserID；多用户以 `|` 分隔（如 `zhangsan|lisi`），或使用 `@all`（按企业微信规则）。
4. 系统启动时由 `WeChatNotifier` 读取配置，使用 wechatpy 企业微信客户端登录鉴权：
   - `from wechatpy.enterprise import WeChatClient`
   - `WeChatClient(corp_id, secret)` 初始化后由 SDK 自动管理 AccessToken。
5. 执行连通性自检：调用一次测试文本消息发送（如 `client.message.send_text(agent_id, user_ids, content)`）。
6. 自检成功则标记通道状态为可用；失败则记录错误码/原因，并将通道标记为不可用，阻断后续真实告警发送或按约定降级。

## 业务规则
- V1.0 默认采用企业微信应用消息主动推送（wechatpy `WeChatClient`），不强制实现公众号模板消息。
- 发件人凭证（CorpId / AgentId / Secret）与收件人（ToUser）均来自环境变量，敏感信息不入库明文展示。
- AccessToken 由 wechatpy 内部自动刷新；进程内可使用默认 MemoryStorage，无需业务侧手动换票。
- 缺少任一必填配置项时，通道视为未就绪，不得假装发送成功。
- 收件人变更后，下次发送立即生效，无需重启以外的额外手工步骤（若配置热加载未实现，则重启进程后生效）。
- 本故事只负责通道可用性；买点/卖点文案发送分别由用户故事 08、09 负责，频控与勿扰由用户故事 10 负责。

## 验收标准（Given-When-Then）
- Given 已正确安装 wechatpy 并填写合法 CorpId/AgentId/Secret/ToUser，When 执行通道自检，Then 测试消息发送成功且通道状态为可用。
- Given 缺少 `WECHAT_SECRET` 或 `WECHAT_TO_USER`，When 初始化通知组件，Then 通道标记为未就绪并给出明确缺失项提示。
- Given Secret 错误或应用无权限，When 登录鉴权或发送测试消息，Then 捕获 wechatpy/企业微信错误码并写入可追踪日志，通道标记为失败。
- Given 配置了多个收件人 UserID，When 发送测试消息，Then 所有合法收件人均可收到（或按企业微信返回的 invaliduser 列表给出部分失败说明）。
- Given 通道已配置可用，When 后续买点/卖点流程调用 `WeChatNotifier`，Then 可复用同一客户端与配置完成发送，无需重复手工登录。

## 非功能要求
- 安装与配置步骤可在 README / `.env.example` 中被完整复现。
- Secret 等凭证不得出现在前端页面或普通业务日志明文中。
- 鉴权与测试发送应有超时与错误分类，便于排障。
- 通道自检失败不得拖垮主进程启动（可降级为“告警通道不可用”状态）。

## 关联数据 / 配置
- 环境变量：`WECHAT_CORP_ID`、`WECHAT_AGENT_ID`、`WECHAT_SECRET`、`WECHAT_TO_USER`
- 组件：`WeChatNotifier`（封装 wechatpy `WeChatClient`）
- 参考：
  - 安装：https://wechatpy.readthedocs.io/zh-cn/stable/install.html
  - 企业微信快速上手：https://wechatpy.readthedocs.io/zh-cn/stable/enterprise/quickstart.html
  - 主动调用接口：https://wechatpy.readthedocs.io/zh-cn/stable/enterprise/client.html
