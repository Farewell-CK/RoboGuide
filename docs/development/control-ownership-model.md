# Control 所有权模型与可回放测试

Control 的 reservation 是唯一资源承诺来源。Assignment、Context binding 和 pending recovery
commitment 引用同一 authority；不能要求所有 reservation 随时都有 active assignment。

| 阶段 | 合法状态 |
| --- | --- |
| 普通 Commit 未 Bind | 资源已承诺，Task 尚无 assignment；投影为 Committed |
| Task-bound | 精确 `(Group, TaskRef, RoleId)` 对应一个 binding；投影为 Bound |
| Context-owned | 多 Task 可引用同一 Context binding，但仍只有一个 owner |
| 解绑等待恢复 | 原绑定已在调用方证明停止/隔离后释放；保留 previous owner |
| Recovery Commit 未 Rebind | replacement 资源承诺与精确 pending handle 同时存在；投影为 RecoveryPending |
| Abort | 只移除该 pending commitment 的 replacement，其他 Task 不变 |
| Task/Group Released | 对应 Task 或 Group 的所有权结束，Context 只能按自己的作用域释放 |

测试模型独立预测以上阶段的资源 owner、投影 phase、assignment 和 pending handle，并通过
公共 snapshot/getter 对比真实 Control。两个 Task 使用同名 Role 和同一初始 Node，但资源
保持独占。固定前缀保证双 Task 的交错 Commit/Abort/Rebind/restore 被实际执行，再用固定
种子生成后续操作；无效生成前置条件被过滤，不要求生产 API 接受不合法组合。

Group 的 Blocked/Adapted 是聚合生命周期：当前一次 Rebind 会转为 Adapted，即使另一 Task
仍 unbound。驱动另一个恢复操作前需要显式 Blocked barrier。本包记录并遵守现状，不引入
“Active/Adapted 必无 unbound”的新要求，也不通过测试私自改变生命周期。

```bash
cargo test -p control ownership_model
cargo test -p control ownership_model_extended_traces -- --ignored
```

普通运行覆盖 256 条固定轨迹；扩展运行覆盖 10,000 条。失败时报告 seed、资源 profile 和
缩减后的操作序列，并将完整/缩减 JSON case 写到临时目录。可通过
`ROBOGUIDE_OWNERSHIP_REPLAY_PATH=<case.json> cargo test -p control ownership_model_replay_saved_case -- --ignored`
单独回放。操作回放不调用物理执行、模型或 E6。

## 复现后修补的边界

在 `bd2a6db` 加入负向测试后，复现以下失败：未知 Task 的 execution recovery 请求在拒绝
前追加证据；Context-scoped 恢复在 release 拒绝前把 Group 改为 Blocked；legacy checkpoint
允许无额外资源的重复 Role；零资源 pending recovery checkpoint 可以指向未解绑的 sibling
Task。新增前置 release 校验与 exact-unbound restore 校验只拒绝违反既有作用域/唯一性
合同的状态。合法恢复的事件顺序、权威和 checkpoint wire shape 保持不变。

正常 Commit 尚未 Bind、Context 复用和零资源 pending commitment 均继续合法。Unknown、
Cancel receipt、restart 和迟到事实的物理停止要求由原 Runtime/application 测试保持；
这里的 Control-only 驱动明确假设调用方已有实际 stop/fencing 证明，不能作为设备证明。
