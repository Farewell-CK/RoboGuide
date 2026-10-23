# ADR-0167：内容库 owner 与共识成员绑定

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0159](0159-original-content-quorum-and-raft-reference-lifecycle.md)

## 决策

完整内容 target 的 member→store owner 必须来自确切当前共识 membership 中的成员
记录，不能来自与 membership 分离的调用者 map。使用显式 `ReplicaMember` 保留原
`addr`，新增可缺省的 `content_store_owner`。缺省编码与 OpenRaft BasicNode 的原 JSON
完全相同；缺省只表示旧资料没有内容库绑定，不能解释为已存在当前内容库。

新 owner 必须为实际 member-local root 的完整 `replica-content:` identity，由未来
显式完整内容 profile 的配置、真实 root 验证及共识成员事务绑定。该资料本身仍非 CAS
bytes、mTLS 或实际持久化 ACK；原 adapter 必须从真实已打开 root 生成各 member 的
完整 metadata+bytes 观察，重新核对 epoch、membership、Root identity 和完整 inputs。
current/joint 每组仍要求同一批完整 members 的严格多数，不能拼接两种多数。

旧 metadata-only profile 与现有 direct-storage v4/v5/v6 一律拒绝隐藏的新 owner
membership；缺省的旧 bytes、原 Snapshot digest 与 journal meanings 保持。先接通
类型、target 校验与旧 profile fence；生产 v0.4 在真实传输、完整恢复/子进程接线与
readiness 具备前继续拒绝，不以新增 DTO 或本地文件结果宣称已启用在线能力。

完整新 profile 的初始与 learner 成员将把声明的实际 store owner 纳入 Raft member
data；它不按 robot/Node 名称或固定设备数量选行为。启动 profile identity 与已提交
membership 的内容绑定分别校验，避免使用尚未创建的随机 root identity 计算其自身
创建所需摘要。原部署 profile 迁移须显式保留旧身份/历史，不静默重算原记录。

## 验证

逐字节核对旧 BasicNode、membership 与 SnapshotMeta 的 JSON；完整 target 缺绑定、
owner 被替换、独立 map 与 committed member 相冲突、joint 两组/disjoint majority
均拒绝。实际旧存储/探针/原 Snapshot/Archive/parent 接管组合继续验证原 digest。
正常旧网络共识和 certificate/transfer 保持原要求。合成 DTO 测试不创建 Node、设备
或网络 ACK；真实网络接线完成后另以实际 mTLS/CAS/原 SQL 与进程结果验收。
