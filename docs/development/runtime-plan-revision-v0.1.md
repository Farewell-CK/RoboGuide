# 运行期计划修订与原目标验证 v0.1

设计：[ADR-0120](../decisions/0120-versioned-runtime-plan-revision.md)。当前协议完成版本化
候选、实际权限 Review、预审替代计划、安全切换和独立原目标 gate。自动生成新 MI 候选、
原 Group-scoped Memory 的新 Group 交接及完整 F8 组合仍继续实施，不能据此关闭整个 F8。
新 MI 工作另用[显式 v0.2](runtime-mission-intelligence-v0.2.md)授权；本 v0.1 不隐含其权限。
这不是设备部署或实验 profile。

## 权威与原目标

Orchestration 保留完整原 published plan、原 admission Group、accepted_at 和已满足事实，
另建 current revision/Group；Control 继续独占资源和 ActorBinding；Runtime 原 attempt/
command 不删除。新的 Task ID 必须从未用于这个 Mission；已满足 Task 逐字段保留，不能
用改名重做相同 operation/parameters。Context 的安全/占用和 Actor/grounded identity
合同固定；节点和资源仍经 Match → Propose → Commit → Bind 选取。

修改未完成 DAG 并不能独自证明原目标仍然满足。策略首次安装时，Control 冻结原
MissionGoal、goal context digest、精确 verifier/predicate 和实际独立 source。source 的
producer 不得是策略安装者或 proposer。Orchestration 安装独立完成 gate：当前所有 Task
满足后仍保持 Running，收到当前版本/Group/完整真实 attempts 的独立 final-positive
证据才完成。源时间仅保留，不能与 Controller 时钟比较。

Unknown、后续序号的非阳性观察、过期或撤销来源不能完成 Mission。最终负向目标结果在
全部 Task 已满足后进入 hold 或原策略批准的替代路径，不伪造 Task execution failure。
历史已完成 Mission 不因之后的来源过期/撤销而被改写；原始完成 receipt 和双时钟决定
证据必须完整恢复。goal gate 不因修订策略本身到期或撤销而消失。

## 原期限与安全切换

当前时间 profile 明确要求 Linux 原 boot ID、实际 time namespace 和 `/proc/uptime`
boottime；不使用固定 epoch 或假定跨机同步。预算扣除实际采样精度，重启不刷新；换 boot/
namespace、倒退或不可读时不授予新工作。迁移到另一时钟域须后续显式合同，不能自动续期。

策略固定原 issuer、tenant、实际证书 notAfter、原授权 registry/file、部署 digest 和总预算。
候选 ticket 在生成前消耗；拒绝、超时、中断均不返还次数。原 command 重放返回原 response，
其中 remaining_ms 是历史值，客户端应 GET 当前 ticket 余量，不可据旧 response 重新工作。

切换先保存 exact candidate/Review、原 source membership/current attempts、之前 attempts
的停止/撤销覆盖、新 target Group 和原预算。先 hold，再 typed stop 与完整原 Memory revoke；
停止接纳、terminal 和独立 safe-state 是不同事实。必须全部确认，且 current owner/session/
完整 generations/State/权限/原预算仍满足，才在单一应用事务提交新版本。确认前不释放承诺，
不发送新 Execute。超时或证据不确定保留 manual hold 和所有权。

旧 Group 标记已被替代并 Released，保留原 Task 状态和全部 Runtime history；旧 Group 的
Execute outbox 永久被拦截。已满足事实只从真实原 Group 继承，后续原 Group outcome 不推动
新版本。新 Group 重新准备 Memory 和真实运行资格。旧 Group-scoped manifest 目前提前拒绝，
不能假装它已经获得新 scope；完整 immutable-child handoff 继续实施。

## 接口

机器可读 envelope：
[修订命令](../../contracts/mission/runtime-plan-revision-v0.1/command.schema.json)与
[原目标 evidence](../../contracts/mission/goal-evidence-v0.1/evidence.schema.json)。它们不代替
当前身份、真实完成/停止、原时钟预算和完整 published-plan 的语义校验。

入口使用实际 mTLS、稳定 principal、不可变 Mission tenant 和原 writer 内的当前授权。
新权限不由 raw loopback 或 HTTP role header 授予；自动 tick/故障触发也在原 writer 内
重读实际原 role profile。证书轮换不改 command 主体或重做副作用。

`GET /v1/missions/{id}/plan-revisions` 返回 current/original Group、计划与 digest、实际
completed set、版本摘要、原 goal contract/context、候选余量、switch 和 goal evidence 状态。
`?candidate={ticket_id}` 返回原候选的完整 published bytes、provenance、digest 和 Review，
便于实际 reviewer 审查。查询不会消费次数或刷新预算。

`POST /v1/missions/{id}/plan-revisions` 使用封闭命令：

```json
{
  "schema_version": "roboguide.runtime-plan-revision/v0.1",
  "command_id": "immutable-caller-id",
  "expected_revision": 1,
  "action": {"kind": "request-candidate", "ticket_id": "original-work-ticket"}
}
```

| action.kind | 当前权限与行为 |
| --- | --- |
| install-policy | recovery-admin；仅一次冻结 policy/独立 goal source，初始 expected_revision=0 |
| revoke-policy | recovery-admin；不可逆缩减修订权限，不释放所有权或移除 goal gate |
| request-candidate | 原 proposer；先持久 work ticket，再允许生成 |
| submit-candidate | 原 ticket 主体；保存原输出/provenance 与验证拒绝，不能自行批准 |
| review-candidate | recovery-admin；精确 ticket/digest、决定、原因和实际 completed precondition |
| approve-alternative | recovery-admin；预审完整候选及声明的未来 completed precondition，不制造完成事实 |
| request-switch | 原 proposer 或 recovery-admin；冻结原 source/target，持久 hold/stop/revoke |
| commit-switch | 原 proposer 或 recovery-admin；核对原 switch 的完整实际安全与期限门禁 |

policy 包括独立 `goal_verifier`、原 `goal_context`、policy_id、proposer、lifetime_ms、
candidate_limit/timeout_ms/bytes、switch_limit/timeout_ms 和显式 automatic_triggers。
当前 typed triggers 为 `local-execution-failed`、`satisfaction-rejected`、`runtime-unknown`。
自动切换只消费实际预审的 exact alternative，生成新候选的完整自动链路尚须后续实现。
当前 MI provenance 是实际 proposer 提交的绑定声明；它不代替后续真实 provider-call SQL
与原始输出证据。手动候选明确标记 operator，不制造模型调用。

输入上界：policy 100ms–1h；最多 16 候选与 16 次切换；候选 1–256KiB，生成/切换预算各
100ms–5min；原 context ≤64KiB；全局原候选 bytes ≤16MiB；128 个 retained policy；每
policy 最多 256 个 command receipts，其中最后八个位置保留给撤销。耗尽明确拒绝，不
静默删历史或刷新次数。

`POST /v1/mission-goal-evidence` 使用 `roboguide.mission-goal-evidence/v0.1`，绑定原
contract_digest、实际独立 source/revision、Mission/tenant、当前 plan_revision/group/digest、
完整 attempts、predicate、sequence、verdict/finality、source-local observed time 和原
receive-relative valid_for_ms。若 source 要求 CAS，必须实际验证完整原字节和长度。
Controller 在完整请求到达时固定 receive/kernel budget；排 writer、授权与 CAS 校验期间
的耗时都不能变成新期限。重复输入返回原 receipt；旧版本原有效 scope 的输入保留为历史。

原切片使用 wrapper v43；当前 wrapper v44 保留其原 history/预算并另保护 v0.2 生成权威。
inner v23 和 event codec v28 保持原义，旧 writer 不能忽略新增权限，包括零事件 policy。
旧 v42 不含这些权限时可迁移；旧 wrapper 含 revision/goal authority 必须拒绝。
支持显式候选和软件进程路径不等于模型有效性、设备停止、硬实时或完整 SoftwareComplete。
