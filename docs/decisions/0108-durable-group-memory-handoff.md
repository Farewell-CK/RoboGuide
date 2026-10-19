# ADR-0108：持久 Group Memory 交接与成员代际门禁

状态：Accepted。2026-10-19 用户授权继续实施成员变更与跨 Group Memory handoff。

## 决定

Application 组合现有 Control、Runtime、停止、Memory preparation/use/import 权威，不建立
第二套资源或成员注册表。交接请求指定完整 source/target Group 代际、source 当前 attempt
集合、已发布 parent、不可变 child、原策略 revision、稳定 principal/tenant 和绝对 deadline。
两 Group 必须属于同一 tenant；目标已由正常 Match/Propose/Commit/Bind 绑定，但尚未 Execute。
目标的完整当前 attempt 集在接纳时冻结，后续不能用增加成员或替换 attempt 扩大该授权。

流程为 `stopping → importing → ready → committed`，异常进入不可自动恢复的 `manual`。
接纳事务保留 source 所有权、建立完整 safety hold/Cancel，并撤销全部原 context/provider
许可。只有每个原 attempt 的独立 StopConfirmed 和全部原 provider revoke/context ACK 齐全，
才可发布带完整 parent lineage 的新 child。自然 Completed、Cancel receipt、Unknown、超时
或软件内容撤销均不代替独立停止证明。SoftwareProcess 证明只用于声明该 assurance 的软件
operation，不升级为设备停止。

child 保留原 owner/provider、内容 digest/size、媒体/内容合同及生产 provenance，仅改变
selector、Group scope 和创建时间。源 manifest 不改写、不变 Global；仍需原 parent 的
Handoff 权限与 child 的独立目标 Group Import/Read 策略。普通 Artifact alias 入口继续拒绝
Group 间扩权。typed-map 不伪装成 generic alias：目标所需地图仍携带原 Global typed-map
及完整原 provenance，经现有 preparation/Control gate 单独验证；本合同不改变地图 schema。

发布事件与 handoff checkpoint 在同一 SQLite authority 事务中保存，提交后才安装 catalog
投影。崩溃后从相同事件恢复；投影失败须 fence，不能保留可 Execute 的半笔交接。
目标沿原 preparation → durable Node ACK → 原 import → actual original-backend probe →
current-use 链路准备；整 Group 的所有当前 attempt 都通过后才 ready。显式 commit 重验
source stop/revoke、两端成员/attempt、原策略、当前 State、接收方当前 session/provider 和
所有最早 deadline，提交后才允许目标 Execute。资源始终由 Control 原 Commit/Bind 持有，
若需替换成员仍必须经过原 stop/revoke 与 Match/Propose/Commit/Rebind，交接不选择 Node。

source 不自动恢复。pending/manual 交接阻止 target Execute；失败/超时先保留双端 ownership
和原证据，fence/revoke/hold，后续只能显式取消/对账。成员或策略变化不能续期或隐式重开。
成功的历史交接不会阻止目标正常 Task 完成/释放。首批接收方完整 retire（原 context/provider
撤销确认、绑定释放/替换、正常完成或独立 stop）后，后续已接受 Task 可通过新 Control
绑定、当前策略、新 preparation/use 获取权限；旧 lease/probe/首次交接预算绝不复用。
首次 commit 后、原 Execute 尚未投递时，期限或原权威变化仍进入 manual，保留原 commit
回执和接收证明，不能将曾接纳 commit 当成迟到 Execute 的授权。

## 接口、兼容和验收

管理入口 `POST /v1/memory/handoffs` 和 `POST /v1/memory/handoffs/{id}/commit` 使用实际
writer gate 下的 recovery-admin、稳定 principal、tenant 与原请求身份；精确重试只返回原
结果。只读 `GET /v1/memory/handoffs` 有 tenant 过滤、分页和字节上限。持久总量有界，
deadline 由实际接收时钟与排队耗时检查。wrapper v40 与 codec v27 fence 旧二进制，空事件
authority 也不可被旧 writer 覆盖。Node 协议与 journal 不增加隐藏语义。

验收包括停止/权限分离、完整成员集合、幂等/跨 tenant、deadline/代际/策略变化、SQL 回滚、
restart、旧 binary 拒绝，以及实际两软件 Node/mTLS/CAS/文件/CPU/独立 verifier 的交接。
不得把纯合约 fixture、软件线程退出或本机进程检查报告为设备、安全或跨主机物理连续性。
本工作包不关闭整体 F4、F5–F10 或 HardwareEvidencePending。
