# C0C-B1 Post-fix Revalidation — Closure Note

- Fix commit: `2f19e8a5cd7d0df8e86d691fb0d0727ccbfd2f88`（`fix: fence dispatch
  replay and late recovery outcomes`）
- Method: 使用**未修改的冻结脚本** `commands/run-c0c3.sh`（md5 见 `head.txt`），
  在完全 fresh 状态下独立运行 **2 次**：每次均从干净的 integration-server、
  roboguide-node A/B、fake Local EAIOS、fresh controller sqlite、fresh node
  journals 开始（`revalidate-c0c3.sh` 驱动，仅做清理/快照/还原，不改 smoke 逻辑）。
  未复用 Codex 在 /tmp 下的任何运行结果。
- 结果: **2/2 PASS，C0C-B1 失败签名彻底消失，无新 production blocker。**

## 构建说明（取证要点）

同步后 `target/debug` 中二进制 mtime（20:23）早于修复提交（20:32），无法证明包含
修复；已执行 `cargo clean -p integration-server -p roboguide-node` 强制重建
（20:43，源码 = 2f19e8a 工作树，`git status` 干净）。两次 run 均使用重建产物。

## 每 run 判据核对（verify-run.py，判据与 390a1cc 前完全一致）

| 判据 | run-1 | run-2 |
| --- | --- | --- |
| POST /v1/missions → 202 | ✓ | ✓ |
| Task A（task-infer-analysis）Completed | ✓ | ✓ |
| Task B（task-move-gate）Completed | ✓ | ✓ |
| Mission Completed | ✓ | ✓ |
| Node A local dispatch authorization = 1 | ✓ | ✓ |
| Node B local dispatch authorization = 1 | ✓ | ✓ |
| duplicate physical execute（EAIOS dispatch POST/node） | 1 次/node | 1 次/node |
| Runtime RecoveryRequired | 0 | 0 |
| Group Blocked | 0 | 0 |
| live Dispatching 回放造成的 false Unknown | 0（attempts 全部终态 Completed） | 0 |
| controller 进程存活（收尾 GET 全部成功） | ✓（server log 0 字节，无 fatal） | ✓ |
| 双 node 无断连/重连循环（node log session ended 计数） | 0 / 0 | 0 / 0 |
| 事件链 Match→Schedule→Proposal→Commit→Bind→Dispatch→Node 执行→Completed→Satisfied | 双 task 全链按序 | 双 task 全链按序 |
| relations / peer_channels | [] / [] | [] / [] |
| 无 coupling 升级 / 无绑定冲突 | ✓ | ✓ |

事件全景（run-1，共 35 条，run-2 同构）：双 node 注册 → Group/双 Task 注册就绪 →
Task A 与 Task B **各自独立**完成 CandidatesMatched→TaskSchedulingSelected→
ProposalCreated→PlanCommitted→ExecutionGroupBound→MissionActorBound →
双 TaskExecutionActivated → 双 NodeObservation(TaskCompleted) → 双
TaskExecutionCompleted→TaskSatisfied→BindingsReleased → ContextBindingsReleased×2 →
ExecutionGroupCompleted → ExecutionGroupReleased。

## 旧失败签名消失确认（C0C-B1 链条逐环）

```
Dispatching 快照回放 → Unknown("") → RecoveryRequired → Group Blocked
→ late Completed → invalid lifecycle: Blocked → integration-server fatal exit
```

- 两次 run 事件中 `RuntimeExecutionRecoveryRequired` 与 `ExecutionGroupBlocked`
  计数均为 **0**；
- 两次 run 的事件链中不存在任何 recovery/deferral 类事件；
- server log 均 0 字节（fatal 时会打印 `Error: "..."`，对照 390a1cc 前缀证据）；
- node log 无一条 `session ended`（修复前的崩溃重连循环特征）；
- execution-attempts 恰好 2 条且全部 `Completed`（每 task 一条，无 Unknown 残留）。

## 证据目录（before/after 分离，供论文/演进取证）

```
evaluation/control_dry_run/c0c/
├── (原路径) 390a1cc pre-fix 失败证据 —— 未改动，git 已核对无 diff
│   （responses/c0c3-*、run/c0c3/controller.sqlite3 49 条事件、
│     node-configs/node-state/c0c3/* 崩溃前 journal、FINDINGS-C0C.md、summary.json）
└── postfix/                                  ← 本轮 after-fix 证据（本目录）
    ├── head.txt                              baseline SHA、二进制、冻结脚本/config/fixture md5
    ├── revalidate-c0c3.sh / verify-run.py    驱动与判据校验器（evaluation-only）
    ├── run-1/ · run-2/
    │   ├── responses/（POST 202、final mission Completed、attempts、reservations）
    │   ├── events/c0c3-events.json（35 条全链）
    │   ├── logs/（server/node 进程日志，全静默；git add -f 提交，见下注）
    │   ├── run/（controller.sqlite3 + EAIOS JSONL）
    │   ├── node-journals/{a,b}/（单执行、单授权、completed）
    │   └── verdict.json（逐判据机器判定）
    ├── run-{1,2}-smoke-stdout.log
    └── summary.json · FINDINGS-POSTFIX.md（本文件）
```

注：仓库 `.gitignore` 的 `logs/` 规则使 390a1cc 的原始进程日志从未入库（pre-fix
的 fatal 消息以逐字引用保存在 FINDINGS-C0C.md §3b）；postfix 的进程日志作为证据
以 `git add -f` 例外提交。pre-fix 原路径的全部已提交证据经 `git diff` 核对未改动。

## 关闭

```
C0-A Internal invariants    PASS
C0-B Production path trace  PASS
C0-C Runtime HTTP smoke     PASS   （C0-C1/C0-C2 于 390a1cc 通过；
                                     C0-C3 于 2f19e8a post-fix 2/2 复验通过）

C0 Control Dry-run = PASS
Control validation CLOSED
```
