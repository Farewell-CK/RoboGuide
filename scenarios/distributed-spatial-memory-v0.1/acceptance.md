# 双机器狗 Spatial Memory Phase 1 验收定义

> 状态：In Review
> 日期：2026-08-30
> 范围：有人值守、低风险、允许人工停止的真机验收切片

## 1. 用户可见目标

在同一个固定实验区域内，机器狗 A 发布一份不可变地图 revision，机器狗 B 获取该
revision、导入本地系统并证明其处于可用的 localization 状态；随后交换生产者与消费者角色，
用完全相同的 RoboGuide 机制完成 B 到 A。

该目标验证 Spatial Memory 的跨 Mission 连续性，不把地图 bytes、Robonix 文件布局或 ROS
服务名放入 MissionPlan、Execution Group、Runtime checkpoint 或 Node Protocol。

## 2. 验收单元

Phase 1 由两条独立且对称的 directed flow 组成：

```text
A -> B:
  producer Mission A: build -> prepare-output -> publish(map-a/r1)
  consumer Mission B: stage -> import -> localization.verify(map-a/r1, anchor-lab)

B -> A:
  producer Mission B: build -> prepare-output -> publish(map-b/r1)
  consumer Mission A: stage -> import -> localization.verify(map-b/r1, anchor-lab)
```

每个 Mission 默认创建一个 Mission-level Execution Group；Task 完成只释放 Task-scoped
binding，不能销毁仍在运行的 Group。Producer 与 Consumer 使用预分配的
`(MapId, MapRevisionId)`，动态 output binding 不属于本切片。

## 3. 节点与职责

| 组件 | 必须声明的事实 | 不拥有的权威 |
| --- | --- | --- |
| Controller | Node observation、Matching、Proposal、Commit、Group/Task projection | Local How、map bytes |
| Artifact Service | immutable manifest、CAS bytes、replica evidence | Task lifecycle、资源承诺 |
| dog-a `roboguide-node` | build/publish/import/verify capability、resource、readiness | replacement decision |
| dog-b `roboguide-node` | 与 dog-a 对称的 capability、resource、readiness | replacement decision |
| Robonix map adapter | vendor API、地图目录、local execution handle | Mission、Group、State authority |

逻辑 Actor 通过版本化 deployment placement constraint 固定到不同物理 Node。Actor 不是
Node selector，placement 不创建 reservation 或 binding。

## 4. 物理与部署前提

- 两台狗位于同一个已标定的固定实验区域，`anchor-lab` 的含义和 frame 关系已记录；
- 每台狗只运行一个 `roboguide-node`，Local EAIOS 通过声明式 adapter 接入；
- Robonix mapping/localization runtime、Zenoh Router 与所需 ROS service 均能被 readiness
  observation 独立证明；仅有进程、WebUI 或 TCP 端口存活不算 ready；
- Controller、Artifact Service 与两个 Node 数据路径可达；SSH tunnel 不是架构要求；
- map/revision identity 在运行前预分配，目标本地地图名不会与无 provenance 的旧地图冲突；
- 操作员能够观察现场并执行人工停止。

## 5. 正常路径证据

每个 directed flow 必须保留以下可关联证据：

1. 文本 Mission Request、澄清结果、最终 MissionPlan revision/digest；
2. 一个 Mission-level Group 和完整 TaskExecution DAG；
3. Candidate Set、Scheduling Decision、Proposal、Commit、Role binding；
4. stable logical execution identity，以及每次 physical attempt identity（RT-G3 完成后）；
5. Node Accepted/Started/Completed facts，按 sequence 单调归约；
6. published manifest、SHA-256 digest、byte size、producer provenance 和固定 anchor；
7. consumer `Staged -> Imported` replica evidence；
8. active map identity、localization mode、pose-quality 判据与 frame/anchor 关系的结构化
   verification evidence；
9. Orchestration 根据完整 DAG 明确完成 Mission，随后 Group terminal release。

任何一项缺失时，不得只根据 `has_map=true`、进程在线或 Task 数量推断成功。

## 6. 故障矩阵

| 注入/现象 | 必须观察到的行为 | 当前状态 |
| --- | --- | --- |
| Zenoh Router 或所需 ROS service 缺失 | 对应 contract readiness=false，后续 Matching 不选择该节点 | v0.4 链路已实现，真机 probe mapping 待验证 |
| Artifact digest/size 不匹配 | Node 拒绝 staging/import，不产生 Imported/Verified evidence | 已有离线覆盖 |
| 相同 artifact 重复导入 | 以持久化 digest 证明幂等，不重复改变本地地图 | 已实现，待真机复验 |
| 同名地图无 provenance 或 digest 不同 | fail-closed，不覆盖本地地图 | 已实现 |
| local completion 后远端 evidence 结果不明 | execution 进入 Unknown/ReconciliationRequired，不自动重放 Local How | 已实现 fencing |
| Node session 丢失或 Controller/Node 重启 | 旧 attempt 被 fence，外部 recovery decision 决定 resume/rebind/fail | RT-G1/2/3/5 待闭环 |
| cancel 与 completion 竞态 | 不伪造 Cancelled，保留最终物理事实 | RT-G4 待闭环 |
| active map/mode/quality/frame 任一不符 | verify Task 失败，不产生强 Verified evidence | evidence 合同/持久化/State 已实现，Node/Adapter mapping 待闭环 |

## 7. 验收指标

- A 到 B、B 到 A 各完成一次全新 selector 的正式 directed flow，成功率为 2/2；
- 每个 Mission 恰有一个默认 Execution Group，重复提交不产生第二个 Group；
- producer manifest digest 与 consumer staged bytes digest 逐字节一致；
- 同一 execution/attempt 不产生重复 Local EAIOS dispatch；
- 两个节点的 committed assignment 与 placement constraint 一致；
- 所有成功结论均能从持久化 event trace 和 State projection 重建；
- 记录各阶段耗时，但 Phase 1 不用两次样本冻结生产级延迟 SLO。

## 8. Non-goals

- 在线增量地图同步、map fusion、active-map 自动选择、地图删除或 GC；
- 复杂 Mission 拆分多个 Execution Group；
- Actor 在两台物理狗之间自动迁移；
- 无人值守运行、生产级 HA、安全认证、身份认证或传输安全；
- 把 Robonix/ROS/Zenoh 的 Local How 提升为 RoboGuide canonical contract。

## 9. Validation Ladder 与退出条件

1. `Offline`：Fake Driver/virtual clock 覆盖 readiness、evidence、幂等、冲突和恢复 fencing；
2. `Process`：本机启动 Controller、Artifact Service、两个 Node fixture，输出完整 event trace；
3. `Hardware`：设备在线后执行全新 A 到 B、B 到 A selector，不复用直接 adapter 调用残留；
4. `Fault`：至少注入 Router 缺失、digest mismatch、session loss 和一次显式 cancel。

只有 2/2 directed flow、全部结构化证据和故障矩阵预期都满足，才能把本切片从
`In Review` 改为 `Accepted`。即使本切片 Accepted，在 RT-G1 至 RT-G8 全部关闭前，也不能
把 RoboGuide 描述为无人值守的稳定真机 Runtime。
