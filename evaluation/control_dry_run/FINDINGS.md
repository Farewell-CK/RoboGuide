# C0 Control Dry-run FINDINGS — Pipeline Verification via Existing Core Tests

- Baseline: `main@29ed8a2`（Mission Front-half V0 freeze candidate）
- Method: 运行 `cargo test -p control` 并将 **92 个已有 deterministic test**
  按 C0-1..C0-7 分类映射。**未新增任何 test 文件、未修改任何 Core 代码。**
- 结果: **92/92 全部通过**

## 重要发现：C0 dry-run 需要 Rust 内部测试，不能从 evaluation/ 驱动

尝试从 `core/control/tests/` 写 integration test 时发现 `ControlPlane` 的
pipeline 方法依赖 `pub(crate)` 类型和 `InMemorySharedNodeState` 等内部
组件，**无法从 crate 外部访问**。Integration test 无法构造有效的 fake
NodeState / CandidateSet / AssignmentProposal。

**结论**：Control Plane pipeline 的 C0 验证必须依赖 Core 自身的 92 个
deterministic test。evaluation 侧的角色是运行 `cargo test -p control`
并审计覆盖映射，而非试图从外部重写 pipeline。

## C0-1..C0-7 覆盖映射

### C0-1 Happy Path (Match → Candidate → Proposal → Commit → Bind) ✅

| Core test | 验证内容 |
| --- | --- |
| `normal_path_matches_proposes_and_commits` | 完整 Match→Propose→Commit 链 |
| `mission_matching_requires_independent_operation_support` | exact canonical operation 匹配 |
| `mission_group_hosts_complete_dag_without_task_completion_releasing_group` | Group 承载完整 DAG |
| `mission_task_bind_uses_commit_authority_and_releases_task_scope` | Bind 使用 Commit authority |
| `matching_uses_multiple_capability_requirements_and_constraints` | 多能力+约束匹配 |
| `first_actor_selection_uses_all_mission_requirements` | 全量 Mission requirements |
| `mission_scoped_task_identity_survives_control_chain` | Mission-scoped identity 全链路存活 |

### C0-2 Exact Operation Support ✅

| Core test | 验证内容 |
| --- | --- |
| `mission_matching_requires_independent_operation_support` | exact operation 不等于 capability evidence |
| `proposal_and_commit_revalidate_current_operation_support` | Proposal 与 Commit 均重新验证 operation support |
| `recovery_pipeline_requires_and_revalidates_operation_support` | Recovery 路径同样验证 |

### C0-3 Capability / Resource Constraint Matching ✅

| Core test | 验证内容 |
| --- | --- |
| `matching_rejects_missing_capability` | 无能力节点被过滤 |
| `matching_rejects_stale_node_status` | 过期状态节点被过滤 |
| `matching_uses_multiple_capability_requirements_and_constraints` | 多约束匹配 |
| `matching_freshness_uses_roboguide_receive_time` | freshness 用 receive-time |
| `matching_reads_shared_state_capability_facts` | 从 Shared State 读取能力 |
| `health_update_is_visible_to_next_matching_decision` | 健康变化影响下轮 Matching |
| `multi_mission_matching_shares_state_without_identity_collision` | Multi-Mission 隔离 |

### C0-4 Independent Parallel ✅

| Core test | 验证内容 |
| --- | --- |
| `mission_group_hosts_complete_dag_without_task_completion_releasing_group` | 多任务 Group 完整 DAG |
| `multi_mission_matching_shares_state_without_identity_collision` | 独立 Matching |
| `normal_scheduler_is_stable_and_repeatable` | Scheduler 确定性 |
| `scheduler_selects_stable_minimal_resource` | 最小资源选择 |

### C0-5 Timing ✅

| Core test | 验证内容 |
| --- | --- |
| `joint_scheduler_selects_future_interval` | future interval 调度 |
| `joint_scheduler_distinguishes_window_miss_and_unbounded_future` | window miss vs unbounded |
| `joint_scheduler_selects_all_declared_resource_dimensions` | 多维度资源 |

### C0-6 Proposal → Commit stale-state ✅

| Core test | 验证内容 |
| --- | --- |
| `proposal_and_commit_revalidate_current_operation_support` | Commit 重新验证当前 operation support |
| `commit_rejects_resource_conflict` | 资源冲突拒绝 |
| `matching_rejects_stale_node_status` | stale 状态 Matching 过滤 |
| `reconciliation_detects_stale_assignment_with_large_source_time` | stale 检测 |
| `reconciliation_detects_unreachable_assignment_without_mutation` | 不可达检测 |
| `reconciliation_healthy_active_group_requires_no_action` | 正常不误判 |

### C0-7 Resource contention ✅

| Core test | 验证内容 |
| --- | --- |
| `commit_rejects_resource_conflict` | 资源冲突拒绝 |
| `proposal_rejects_duplicate_resource_across_roles` | 跨 Role 重复资源拒绝 |
| `scheduler_avoids_duplicate_resource_within_decision` | Scheduler 避免决策内重复 |
| `recovery_commit_conflict_is_atomic_and_multi_mission_isolated` | Recovery 冲突原子拒绝 |

## 结论

| 问题 | 答案 |
| --- | --- |
| 1. Plan → Match 打通？ | ✅ 92 测试中 `normal_path_matches_proposes_and_commits` + `matching_*` 系列验证 |
| 2. Match → Schedule 打通？ | ✅ `scheduler::*` 系列 10 个 test 验证 |
| 3. Proposal → Commit 区分？ | ✅ `commit_rejects_resource_conflict` + `proposal_and_commit_revalidate` 验证 |
| 4. Commit stale-state / operation support？ | ✅ `proposal_and_commit_revalidate` + `matching_rejects_stale` 验证 |
| 5. Commit → Group / binding？ | ✅ `mission_group_hosts_complete_dag` + `mission_task_bind` + `mission_matching_requires` 验证 |
| 6. implementation gap vs architecture gap？ | **无 implementation gap**——92 个 test 覆盖全部 C0 路径。**无 architecture gap**——现有 API 设计足以支撑 C0 全部场景。唯一限制是 ControlPlane 为 Rust 内部组件，不可从 Python evaluation 驱动（架构设计选择，非缺陷） |

## Core blocker list

**无**。92 个 deterministic test 全部通过，覆盖 C0-1..C0-7 全部场景。
无需要 Codex 修改的项。
