# ADR-0107：原 Memory 撤销的逐 provider 进度与独立只读对账

状态：Accepted。2026-10-19 用户选择原预算耗尽后允许独立、有界的只读对账，并授权实施。

## 问题与决定

原 current-use v0.1 的聚合回执不可变。多个 provider 顺序处理中的一次失联可能跳过后续
provider；聚合 Unknown 一旦保存，原命令重送不能补齐确认。保留 ownership 正确，但需要
一个不会重复副作用、覆盖历史或刷新原权限的恢复入口。

Node 分别持久化每个原 provider 的请求、发送意图、确认与自动查询次数/退避时间。独立
provider 使用最多四个全局 I/O worker；一个失败不跳过其它 provider。原 POST 意图提交
后至多发送一次，崩溃产生的歧义只允许 GET。已确认原证明可离线复用。原撤销观察预算
仍为十秒、每 provider 至多三次自动 GET，原 issue/receive、预算和命令 ID 不变。

新增独立 MemoryUseQuery/QueryReceipt。每条原 revoke 只允许一个观察会话：最长五分钟、
十六个查询代数，每代每 provider 至多一次 GET；单次最长三十秒。操作员通过
`POST /v1/memory/use-queries` 提交 command_id、expected_generation、完整排序的
provider_ids、固定 deadline_ms 和 query_timeout_ms。相同稳定 principal 和请求重试
返回原代数；身份或内容冲突拒绝。会话耗尽不能重建、续期或强制释放。

观察使用已保存的原请求、backend instance、contract 和 preparation。缺少原请求为
NotIssued；查询不能补发 POST。新的观察预算只用于读取原结果，不改变 use/preparation/
import/执行预算。Node 以实际 Linux boot/time namespace 与单调时间保存独立接收预算；
重启不重置查询，重启主机使旧预算失效。backend 的实际原结果仍须逐项验证。

初始 Unknown 原样保留。后续确认追加到独立 journal/Controller wrapper，并单调归约到原
provider 集合。迟到、changed-owner/attempt 的结果仅作历史。Task、整 Group 和恢复路径
仍按各自原作用域检查 context ACK、全部 provider 证明及独立 physical stop gate；观察
不会生成 Task satisfied、stop certificate、新绑定或新的内容使用许可。

## 权威、接口与兼容

Controller Application 拥有查询授权、持久预算与结果消费；Control 独占资源和绑定，
Runtime 保留原 attempt，Integration 只传递协商后的元数据，Local EAIOS 拥有真实效果。
操作员角色、稳定 principal 与 tenant 委托在实际 writer gate 内重新核验；SQL 使用有界
后台工作入口，提交后才能投递。`GET /v1/memory/use-queries` 按 tenant、query_id 分页，
单页上限 100 条/1 MiB；原始证据不作为可用内容权限。

Protocol v0.4 保持；catalog v0.14/config v0.12 显式协商 Node Contract v0.11，新增
Query/QueryReceipt envelope 11/15。v0.10 的原 use command/receipt 仍保持 v0.1 原语义，
旧 Node 不获得新查询语义。原 provider GET 合同不变。

Controller 与 supervisor 共享 wrapper v39；Node journal v11；类型化对账审计事件使用
codec v26。保守读取旧 v38/v10 时不推定不存在的查询/确认。旧二进制拒绝新存储，包括
零事件 authority；复制 profile 的升级仍须显式迁移，不通过重建空集群绕过历史。

## 验收要求

确定性测试验证原命令/预算不可变、逐 provider 覆盖、幂等、租户、迟到和替换 backend
拒绝，以及不影响活跃 peer 的 Task 释放。实际软件进程通过正式 Node/mTLS、真实 CAS、
SQLite 与透明响应丢弃验证原预算内恢复、预算外只读恢复和无法确认时保留所有权。
透明代理只转发真实原 backend 结果，不构造 provider ACK 或设备事实。

软件进程不建立设备停止、硬件身份、物理交接或执行连续性。完整 F4、跨 Group handoff
和 F5–F10 保留独立验收，不由本 ADR 提前关闭。
