# 2026-10-23 精确已提交内容路由

唯一分支 `codex/nonrelease-remediation-closure`，前置 `fb3bd17`。
[ADR-0175](../../decisions/0175-exact-committed-content-routing.md)收紧首版：
`stream_content_to` / `PushContent` 恢复原 leader proposal staging；
`route_committed_content_to` / `PushCommittedContentV1` 专门处理已提交路由。
旧 peer 拒绝新命令，旧 proposal 的权限不扩大。

source 和 receiver 的实际原 inode SQL 都必须匹配 target 的 revision、完整
checkpoint、完整原 candidate identity 和 qualified inputs；Ready 前、每块及
完成时独立复核。原 mTLS/current membership/epoch/budget 保持，回执中 source
member/Root owner/原 TLS leaf 精确匹配实际本次 source，另一个 voter 的证据不可借用。

`exact-content-route-replica-tests.log`：**107 passed**，包含实际 mTLS follower→leader
恢复、旧 follower proposal 拒绝和未提交 target 拒绝。相关 Replica/两 composition
全部 targets/features Clippy 通过；维护 Python checker Ruff/strict mypy 通过。

`exact-content-route-native-01` 在 probe 本地 tagged-enum 的数字成员 key 解码处失败，
尚未传输。原失败/actual wait 全保留；本地 probe 改用已有 Observe 相同的明确 JSON
target 解码，产品 wire 保持完整 typed DTO。后续使用已经冻结的真实 Controller/CAS
源跑独立网络 gate，避免重复不变的 owned-file 整链。

`exact-content-route-native-02/result.json` **通过**。三个实际独立 infrastructure
进程/mTLS/SQL/Root 使用原真实 Controller 的完整来源，隐藏 leader qualified file，
由 follower 经新 command 路由恢复。独立核对原 bytes、source member/Root owner；
再隐藏 source 原文件，同 ID 返回完全相同原 receipt，不重读或续期。revision+1
的未提交 target 拒绝。之后 metadata-only 多数反例、leader 实际 SIGKILL、更新 epoch
完整多数及原 member/inode 重开均通过。新 gate 创建零 embodied Nodes/设备/模型/模拟器。

冻结 probe SHA-256 为
`fc317eae4c2c0753509a7c76a5dd869c79be3c709352832c08fad61273d224c5`，
冻结 storage probe 为 `3b5cf710f1d89306a68c899c6300b373356c673678b21418be085f67408cc85c`。
源路径为 `/tmp/rg-ci-endpoints-20261023-01`，原进程已静止；历史 software Node
记录不转成新注册、当前 use、设备或物理事实。

原证据位于 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。
本包仍不提供公开 Artifact fallback、Prepared takeover 或完整 HA；下一步继续原
Controller 的受权读取与接管，以及其余全部 N1–N7/P1–P7。
