# ADR-0099：整改的真实事务、恢复和调用链

状态：Accepted（软件实现，物理验收独立）。日期：2026-10-14。

## 决定

1. 保持完整 Checkpoint 为恢复权威。Command Journal 在同一 SQLite 事务中提交，revision 独立于事件序号；最后一条 Journal 必须精确匹配 Checkpoint 的 schema、事件高水位和内容 digest。恢复不重新运行调度或重放设备副作用。
2. Application Commit v2 将本事务新增 Journal 行带入现有 quorum 接受、Raft snapshot 和副本物化；保留 v1 的 checkpoint identity hash domain，避免升级重新解释历史 digest。Controller wrapper v27 对新权威作旧二进制 fence。历史 v1 只用于兼容读取，不得隐藏 v27 权威。
3. Mission HTTP 的 tenant 在每一次 Request save 的同一事务中绑定，包含第一次 Received；管理员跨 tenant 操作保留原 owner。HTTP 幂等占位一旦开始处理即不能因异常删除；不明确的结果进入保守恢复，不能自动重复 Provider 或 Controller 副作用。远程 mutation 必须提供 Idempotency-Key。
4. Artifact mTLS 的证书映射同时包含主体、角色、Node/provider 和资源 ACL；Header 只能核对当前 session，不能创造身份。未授予的数据访问默认拒绝；远程非管理员不能枚举完整目录。上传以证书主体作隔离。
5. Node Memory import 在有界独立 worker 中处理，不能阻塞执行事实或 Cancel。持久化后中断的 import 保持 Unknown，未经 provider 的幂等/查询合同不得自动重试本地副作用。Controller 的持久化发令与 receipt reducer 必须先于 application ACK；未接入的扩展不能宣称已完成分布式复制。
6. readiness 检查真实存储和恢复高水位；复制 profile 还要求当前 epoch/quorum。Unknown 设备执行暴露为 degraded，不能被 readiness 清空或重放。

## 迁移与验证

Journal 的历史内容和物理执行状态分别保留。旧 standalone checkpoint 可在校验后进入新事务；复制部署升级必须全体兼容 v2 DTO 和 v27 application schema，旧 profile 不可静默重写。测试须覆盖零事件提交、冲突命令、响应丢失、副本 snapshot/物化、损坏高水位、跨 tenant 与证书越权，以及 import 中断重试。不得用实验或真机成功率替代这些合同测试。
