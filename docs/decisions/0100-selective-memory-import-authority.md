# ADR-0100：有界 selective-import 的 Controller 权威与恢复

状态：Accepted。日期：2026-10-14。实现范围是软件合同，不证明设备、地图融合或物理连续性。

导入由管理员显式提交 exact Group/Task/Role/attempt、consumer Node/provider、完整 immutable manifest、稳定 import_id 和不可续期 deadline。Application 在现有写事务中核对 Control 当前绑定、Runtime 当前 attempt、Mission 非终态/非 hold、注册 provider、manifest scope 和 artifact。Group scope 仅允许相同 Group 的当前参与节点；任意跨 Group、未绑定或历史 attempt 拒绝。它不建立 Group handoff 权限。

Intent、重试预算、receipt 和原始 receive time 在 Controller wrapper v27 内持久化，并与 Journal/Checkpoint 一起 quorum 提交。发令只发生在 Commit 后且重新核对当前绑定。receipt 仅接受原目标 Node 的当前 session，精确 command/import identity，且不能让终态回退；该 reducer 完成持久化后 Integration 才 ACK。

重发只允许相同 immutable identity，不授权重新执行 provider 副作用。传输 epoch 在每次发送时重新 fence；Node journal 的 immutable identity 不含可变传输 epoch，但绑定本地 provider、workflow 和 connection 配置。Node 要求关联的 Execute 已落本地 journal，核对 invocation 的 Group/Task/Role，Unknown 仅回报，禁止自动重置。Controller deadline/预算耗尽保留 Unknown，不能产生 Imported 或删除旧证据。

Admission、Commit、Recovery、Node Protocol、Runtime 的角色分别为：管理员请求意图；Control 核对现有所有权；Runtime 核对当前 attempt；Application 持久化与恢复 outbox；Node Protocol 提供当前 session/epoch 和 receipt；Local EAIOS 保持最终内容和语义权威。导入不改变资源所有权、不建立地图 active reference、不触发自动全量复制。
