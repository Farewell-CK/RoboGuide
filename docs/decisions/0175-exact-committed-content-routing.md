# ADR-0175：精确已提交内容路由

- 状态：Accepted，本包已实现并验收
- 日期：2026-10-23
- 前置：[ADR-0174](0174-committed-member-content-routing.md)

ADR-0174 的首版路由需要补齐两个边界：原 `PushContent` 保持仅原 leader 的 proposal
staging 语义；新 `PushCommittedContentV1` 显式承载已提交内容路由，旧接收者明确拒绝。
source 和 receiver 在接收 Ready 前、每个有界 chunk 及完成时核对实际原 inode SQL
中的 revision、完整 checkpoint digest、原 candidate digest/inputs 与完整 target。
target membership/current epoch 和实际 mTLS 仍独立复核；proposal target 不能变成
非 leader 的 payload 读取许可。原 source member、Root owner 和实际 TLS leaf 必须
精确匹配原接收 stream journal 的 origin，任意当前 voter 的回执不能替代。

原 stream storage 已显式保留 Replica source member/Root owner/leaf，因此不新增或
改写本地 record 及原 Unknown/budget；新 wire command 不创建新的 Artifact owner、
公开内容 grant、GC 或应用 Commit。原 leader staging 与已提交 route 使用独立 API，
相同 ID 仍只查询第一次原 target/input/origin，不重做或续期。

验收覆盖真实 follower→leader 内容恢复、原 source 精确匹配、未提交/变化 target
拒绝且没有新的 reader/stream 意图、旧 proposal 路径拒绝 follower，以及缺字节多数
反例。Prepared/公开读取接管与完整其余整改仍由后续接线和实际组合证明。
结果见[验收记录](../development/acceptance/2026-10-23-exact-committed-content-routing.md)。
