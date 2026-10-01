# ADR-0055: 完整请求体绑定的 Controller 接纳对账

- Status: Implemented
- Date: 2026-10-01

Controller 的普通 Mission 查询只能证明身份存在，不能证明其接纳的是当前审查过的完整
MissionPlan。新增只读 `GET /v1/missions/{mission_id}/admission` 返回
`roboguide.controller-mission-admission/v0.1`：Mission、Group、实际接收的完整 HTTP body
SHA256 和 Controller-local 接纳时间。接纳事务在事件/Orchestration checkpoint 同一
SQLite transaction 保存该记录，提交后才可 dispatch 或返回 202。原计划、调度和资源
authority 均不改变。相同计划的幂等提交不覆盖第一次接纳的原始 body digest。

Mission Service 在 HTTP transport 发出 POST 前保存实际 prepared `Request.data` 摘要。
恢复只做一次 GET，将 authority receipt 与原 POST body、当前完整 draft digest、Mission
身份交叉核对；匹配才恢复 Accepted。404、错 digest、错 identity、旧版本无 receipt 和
查询故障继续保留 submission fence，不授权另一个 POST，不调用模型。

`observations/v0.3` 新增独立 admission evidence。原始 status、transport error 与失败证据
不被改写为成功回执。B1 同时支持真实原 POST 接纳或完整绑定的权威 receipt；task 注册、
semantic identity、review snapshot、execution identity 和 verifier 检查仍独立执行，
Formal admission 与 benchmark success 规则不变。普通 identity lookup 仍不能替代接纳。

Controller wrapper checkpoint 升为 v19，兼容 v16-v18，但这些旧数据没有原始 HTTP
receipt 时查询返回 unavailable，不从归一化计划反推请求体。恢复核验记录的 Group 和
Orchestration 当前身份。该证据依赖可信 Controller endpoint/数据库，不是外部签名认证。

离线测试覆盖生产 HTTP handler、checkpoint replay、幂等/冲突提交、发送前中断、丢失
回执、错误摘要/身份/schema、404/5xx，以及完整 B1 gate 的正常和拒绝路径。未运行真实实验。
