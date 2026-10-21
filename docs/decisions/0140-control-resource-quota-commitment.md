# ADR-0140：Control 资源承诺与 tenant quota 原子对账

状态：Accepted。

## 背景

Control 已有可 checkpoint 的 tenant/category `QuotaLedger`，但此前没有正常 Commit、Group Commit、
Recovery Commit 或生命周期 Release 接线。配置的 limit 因此不能约束实际 reservation；手工 claims
也可能与物理资源占用脱节。

## 决定

每个带资源的正常 Commit、Mission Group Commit 与 recovery commitment，在写入 reservation 前，
按当前已验证 Proposal 或原 Execution Group Role 的 `ResourceRequirement.kind/units` 构造精确 quota
claim。稳定 claim identity 绑定全局唯一 `ResourceId`，charge 到 Mission 的不可变 tenant；仅没有
身份/tenant 部署 authority 的显式 legacy profile 使用 `local`。配置了 identity registry 却没有
Mission tenant 的请求 fail closed。

若 tenant/category limit 已配置，所有该 tenant/category 下已承诺资源的 demand units 累加，超额时
原子拒绝：不写 reservation、不生成 Commit event、不创建 recovery pending authority。没有配置 limit
仍会保留实际 claim，便于日后精确核算；limit 更新按现有 claim 检查，不能低于已使用量。资源 id 在
State 中全局唯一且当前 reservation 仍为独占，因此这只记账需求，不拆分共享物理资源，也不让
`Resource.capacity` 变成 divisible quota。

正常 Task、Context、Role、Group 和 recovery-abort 的原释放路径同步释放对应 claim；Unknown、
Blocked、pending recovery 和持续 Context reservation 均保留用量。独立 Allocation projection 仍
是只读视图，不拥有 quota。

Controller checkpoint wrapper v47 保存 ledger、revisioned quota policy 与显式 legacy-unaccounted
fence。每个新 resource claim 另有 typed `resource_id` 关联；不能从可碰撞的 claim 字符串推导
reservation identity。v46 及更早 checkpoint 的既有 reservations 没有原始 demand 证据，restore 后记为 unavailable；
不套当前 requirement/default 推测单位。Recovery-admin 通过 `/v1/resource-quotas` 提交完整
expected-revision policy，使用原 operator authority gate、stable principal、幂等 receipt 与 Control
command journal；read-only `/v1/resource-quotas` view v0.2 列出全部已配置或已有 claim 的
tenant/category，用量、可选上限和可选剩余量，避免无 policy 的真实承诺从视图消失。任何有 quota 的 tenant 仍持有 legacy
unknown demand 时，不得安装该 tenant 的额度；无 quota tenant 可继续独立运行。恢复核对每个
typed resource claim 恰有一个原 reservation、tenant 与 Mission 相符，且 policy history、current policy
和 ledger limits 完全一致，不允许 claim 单独授权 reservation。

## 边界

该 ledger 是持久 Control tenant/category 逻辑配额与 reservation 对账，不是可分 CPU/GPU allocation、
硬件 capacity scheduler、真实 load/traffic/travel cost、OS cgroup enforce、硬实时或物理安全证明。
Local EAIOS 文件 worker 的 POSIX quota 是独立本机进程 envelope；两者不能互相充当证据。

## 验收

`core/control/src/tests/quota_commitment.rs`、integration-server operator regression 与[实际零 Node
进程验收](../development/acceptance/2026-10-21-control-resource-quotas.md)覆盖 limit enforcement、
失败无事件/无 reservation、checkpoint/restart、claim tenant tampering、legacy demand unavailable、
管理员权限、stable-principal 幂等重试、SQL journal 与 read-only query。CI 加入维护的实际进程
gate；它启动真实 Controller 和 operator mTLS，不注册 Node/设备或部署能力。
