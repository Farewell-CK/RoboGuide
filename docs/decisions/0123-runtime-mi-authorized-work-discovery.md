# ADR-0123：运行期 MI 的已授权任务发现

日期：2026-10-20。状态：Accepted，实施中。

ADR-0121 的 worker 只接受部署预填 Request ID，无法处理部署后新接纳 Mission 的实际故障。
增加只读、分页的 `GET /v1/runtime-plan-work`：Controller 从已提交的运行期修订状态投影当前
Generating ticket，按真实 TLS stable principal 与明确 tenant 过滤。队列不包含 Node inventory、
计划正文或模型输出，不创建 ticket、预算、claim、Review、资源承诺或执行命令。

每页最多 32 项，游标为最后一个 Mission ID；当前修订状态上限 128。已撤销、原期限到期、
source revision/Group 改变或不再 Generating 的任务不进入投影。消费前仍走原完整 source/
claim/provider nonce/Review/stop/revoke/Control 校验；列表与消费间的变化只能拒绝，不能续期。
未认证及未明确选定 tenant 的请求拒绝。授权仍由原实际 transport 决定，请求字段不能选择主体。

Mission Service 增加精确 `GET /v1/mission-requests/by-mission/{id}`，利用既有 Mission 唯一索引
及持久 tenant JOIN 返回原 Request ID；不存在、其他 tenant 和尚未迁移的记录统一 404。
要求 viewer 及明确 tenant，不允许全局身份扫描；查询不重新执行模型、创建或提交 Mission。

worker v0.2 必须显式声明 discovery，最多读四页，并以有界顺序处理发现的原 Request；
v0.1 保持手工 ID 的原语义。两种配置均复用原 SQL writer、固定 mTLS peer、原预算和不可重发
调用日志；可读恢复不授予新调用。真实 HTTP/SQL/故障恢复与跨 tenant/主体拒绝需独立验收。
本决定不解决 scoped Memory 修订或设备证据。
