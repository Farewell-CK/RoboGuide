/**
 * Event semantic catalog for the RoboGuide console.
 *
 * The mapping below is derived from the actual Rust implementation:
 * `core/domain/src/lib.rs::EventPayload` (externally tagged serde enum) and the
 * HTTP projections in `apps/integration-server/src/main.rs`. Every variant name
 * in {@link EVENT_CATALOG} exists verbatim in the domain enum; unknown variants
 * degrade gracefully to the "system" category instead of being dropped.
 */

/** Visual category per event family; drives color, ticker filter, and packet style. */
export const CATEGORIES = {
  decision: { label: "决策", color: "#35e0ff" },
  group: { label: "编组", color: "#9d7bff" },
  runtime: { label: "运行时", color: "#ffc857" },
  observe: { label: "观测", color: "#3dffb4" },
  recovery: { label: "恢复", color: "#ff5d7a" },
  system: { label: "系统", color: "#7d8db0" },
};

/** The three V2 semantic chains rendered above the stage; dormant steps stay dim. */
export const CHAINS = [
  {
    id: "chain-decision",
    name: "决策链",
    color: "#35e0ff",
    steps: ["Plan", "Match", "Schedule", "Propose", "Commit", "Bind", "Execute"],
    dormant: [],
  },
  {
    id: "chain-observe",
    name: "观测链",
    color: "#3dffb4",
    steps: ["Observe", "Update", "Fuse", "Believe"],
    dormant: ["Fuse", "Believe"],
  },
  {
    id: "chain-recover",
    name: "恢复链",
    color: "#ff5d7a",
    steps: ["Detect", "Reconcile", "Adapt"],
    dormant: [],
  },
];

/** Stage layer identifiers; mirrors the implemented module responsibilities. */
export const LAYERS = {
  mission: "mission",
  intelligence: "intelligence",
  control: "control",
  group: "group",
  runtime: "runtime",
  nodes: "nodes",
  world: "world",
  smp: "smp",
};

/**
 * Per-Mission accent colors assigned in group-creation order; badges on node
 * cards, group frames, and runtime slot bars all reuse this palette.
 * @type {string[]}
 */
export const MISSION_PALETTE = ["#9d7bff", "#35e0ff", "#ff9f43", "#ff7ac8", "#7ef29a"];

/**
 * Extracts the owning Mission identity from any event payload fields.
 * Every mission-scoped variant carries it either as `mission_id` or inside a
 * `task_ref`/relation endpoint, mirroring the domain field layout.
 * @param {object} fields - Event payload fields.
 * @returns {?string} Mission id or null when the variant is not mission-scoped.
 */
export function missionIdOf(fields) {
  return fields.mission_id
    ?? fields.task_ref?.mission_id
    ?? fields.source_task_ref?.mission_id
    ?? fields.target_task_ref?.mission_id
    ?? null;
}

/** Composite key for mission-scoped tasks (slots, DAG, calendar, state). */
export const taskKey = (missionId, taskId) => `${missionId}/${taskId}`;

/**
 * Normalizes one raw console event.
 *
 * Live events arrive as `{sequence, event_id, timestamp_ms, correlation_id,
 * causation_id, payload_schema, payload}` where `payload` is the externally
 * tagged domain enum; replay traces already use this shape.
 *
 * @param {object} raw - Raw event record from the API or a replay trace.
 * @returns {object} Normalized event with a flat `fields` object.
 */
export function decodeEvent(raw) {
  const payload = raw.payload ?? {};
  const kind = Object.keys(payload)[0] ?? "Unknown";
  return {
    sequence: raw.sequence,
    id: raw.event_id,
    ts: raw.timestamp_ms,
    correlation: raw.correlation_id,
    causation: raw.causation_id,
    schema: raw.payload_schema,
    kind,
    fields: payload[kind] ?? {},
  };
}

/** Extracts a readable `mission/task` identity from a TaskRef-shaped value. */
export function taskRefLabel(taskRef) {
  if (!taskRef) return "?";
  return `${taskRef.mission_id ?? "?"} / ${taskRef.task_id ?? "?"}`;
}

const T = (f) => taskRefLabel(f.task_ref);

/**
 * Event catalog: variant name → { cat, layer, station, chain, rung, task, node,
 * group, cal, label, desc }.
 *
 * - `cat`     ticker category / packet color family
 * - `layer`   stage layer box that pulses on arrival
 * - `station` Control Plane station highlighted (match|schedule|coordinate|groupmgr)
 * - `chain`   [chainId, stepName] lit on the semantic chain bar
 * - `rung`    [level, hotness] on the recovery ladder (hotness: hot|warm)
 * - `task`    task lifecycle override key `{taskRef, status}`
 * - `node`    node effect `{id, state}` with state in registered|offline|hot
 * - `group`   group frame effect (active|blocked|released)
 * - `cal`     calendar mutation `{taskRef, phase, start, end}`
 * - `label`   short ticker headline
 * - `desc`    one-line human explanation with real identifiers
 */
export const EVENT_CATALOG = {
  NodeRegistered: {
    cat: "observe", layer: LAYERS.nodes,
    chain: ["chain-observe", "Observe"],
    node: (f) => ({ id: f.node_id, state: "registered" }),
    label: (f) => `节点注册 ${f.node_id}`,
    desc: (f) => `节点 ${f.node_id} 注册可见（lease ${f.lease_id ?? "?"}）`,
  },
  NodeHeartbeatAccepted: {
    cat: "observe", layer: LAYERS.nodes,
    chain: ["chain-observe", "Observe"],
    node: (f) => ({ id: f.node_id, state: "hot" }),
    label: (f) => `心跳 ${f.node_id}`,
    desc: (f) => `节点 ${f.node_id} 心跳被接受并续租`,
  },
  NodeLeaseExpired: {
    cat: "recovery", layer: LAYERS.nodes,
    chain: ["chain-recover", "Detect"],
    node: (f) => ({ id: f.node_id, state: "offline" }),
    label: (f) => `租约过期 ${f.node_id}`,
    desc: (f) => `节点 ${f.node_id} 租约过期，变为不可调度`,
  },
  CandidatesMatched: {
    cat: "decision", layer: LAYERS.control, station: "match",
    chain: ["chain-decision", "Match"],
    label: (f) => `候选集 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `Capability Matching 产出任务 ${T(f)} 的候选集（Who can）`,
  },
  TaskSchedulingSelected: {
    cat: "decision", layer: LAYERS.control, station: "schedule",
    chain: ["chain-decision", "Schedule"],
    label: (f) => `联合调度 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `BoundedJointScheduler 为 ${T(f)} 选定 ${(f.assignments ?? []).map((a) => a.node_id).join(", ") || "—"}`,
  },
  TaskSchedulingDeferred: {
    cat: "decision", layer: LAYERS.control, station: "schedule",
    chain: ["chain-decision", "Schedule"],
    label: (f) => `调度推迟 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `任务 ${T(f)} 本轮无可行联合决策，保持 Ready（${f.reason ?? "?"}）`,
  },
  SchedulingReservationCreated: {
    cat: "decision", layer: LAYERS.control, station: "schedule",
    chain: ["chain-decision", "Schedule"],
    cal: (f) => ({ taskRef: f.task_ref, phase: "Scheduled", start: f.starts_at, end: f.ends_at }),
    label: (f) => `未来区间 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `Control 日志持久化 ${T(f)} 的未来调度区间 [${f.starts_at ?? "?"}, ${f.ends_at ?? "?"})`,
  },
  SchedulingReservationActivated: {
    cat: "decision", layer: LAYERS.control, station: "schedule",
    chain: ["chain-decision", "Schedule"],
    cal: (f) => ({ taskRef: f.task_ref, phase: "Activated" }),
    label: (f) => `区间激活 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `${T(f)} 的调度区间在 Proposal/Commit/Bind 后激活（planning evidence）`,
  },
  SchedulingReservationInvalidated: {
    cat: "recovery", layer: LAYERS.control, station: "schedule",
    chain: ["chain-recover", "Reconcile"],
    cal: (f) => ({ taskRef: f.task_ref, phase: "Invalidated" }),
    label: (f) => `区间失效 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `${T(f)} 的调度区间被重验证拒绝：${f.reason ?? "?"}`,
  },
  SchedulingReservationReleased: {
    cat: "decision", layer: LAYERS.control, station: "schedule",
    cal: (f) => ({ taskRef: f.task_ref, phase: "Released" }),
    label: (f) => `区间释放 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `${T(f)} 的调度区间随终态释放：${f.reason ?? "?"}`,
  },
  ProposalCreated: {
    cat: "decision", layer: LAYERS.control, station: "coordinate",
    chain: ["chain-decision", "Propose"],
    label: (f) => `提案 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `${T(f)} 的 Assignment Proposal 进入协调（提案 ≠ 生效分配）`,
  },
  PlanCommitted: {
    cat: "decision", layer: LAYERS.control, station: "coordinate",
    chain: ["chain-decision", "Commit"],
    label: (f) => `提交 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `Shared Resource Coordination 提交 ${T(f)} 的资源承诺（唯一 authority）`,
  },
  ExecutionGroupCreated: {
    cat: "group", layer: LAYERS.group,
    chain: ["chain-decision", "Bind"],
    group: () => ({ effect: "active" }),
    label: (f) => `创建执行组`,
    desc: (f) => `为 Mission ${f.mission_id ?? "?"} 创建 Mission 级执行组 ${f.group_id ?? "?"}`,
  },
  ExecutionGroupBound: {
    cat: "group", layer: LAYERS.group,
    chain: ["chain-decision", "Bind"],
    group: () => ({ effect: "active" }),
    label: (f) => `绑定 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `已提交计划绑定进执行组 ${f.group_id ?? "?"}（任务 ${T(f)}）`,
  },
  ExecutionGroupActivated: {
    cat: "group", layer: LAYERS.group,
    chain: ["chain-decision", "Execute"],
    group: () => ({ effect: "active" }),
    label: (f) => `执行组激活`,
    desc: (f) => `执行组 ${f.group_id ?? "?"} 开始执行绑定角色（任务 ${T(f)}）`,
  },
  ExecutionGroupBlocked: {
    cat: "recovery", layer: LAYERS.group,
    chain: ["chain-recover", "Detect"],
    group: () => ({ effect: "blocked" }),
    rung: () => [2, "hot"],
    label: (f) => `执行组阻塞`,
    desc: (f) => `执行组 ${f.group_id ?? "?"} 等待对账：${f.reason ?? "?"}（Blocked ≠ Failed）`,
  },
  ExecutionGroupCompleted: {
    cat: "group", layer: LAYERS.group,
    group: () => ({ effect: "active" }),
    label: (f) => `执行组完成`,
    desc: (f) => `执行组 ${f.group_id ?? "?"} 完成全部角色（任务 ${T(f)}）`,
  },
  ExecutionGroupFailed: {
    cat: "recovery", layer: LAYERS.group,
    group: () => ({ effect: "blocked" }),
    rung: () => [4, "hot"],
    label: (f) => `执行组失败`,
    desc: (f) => `恢复耗尽，执行组 ${f.group_id ?? "?"} 终态失败：${f.reason ?? "?"}`,
  },
  ExecutionGroupReleased: {
    cat: "group", layer: LAYERS.group,
    group: () => ({ effect: "released" }),
    label: (f) => `执行组释放`,
    desc: (f) => `终态执行组 ${f.group_id ?? "?"} 释放全部角色与资源绑定`,
  },
  ExecutionGroupRoleBindingReleased: {
    cat: "recovery", layer: LAYERS.group,
    chain: ["chain-recover", "Reconcile"],
    rung: () => [2, "warm"],
    label: (f) => `部分释放 ${f.role_id ?? "?"}`,
    desc: (f) => `仅释放角色 ${f.role_id ?? "?"} 在节点 ${f.node_id ?? "?"} 上的绑定，组保持存活`,
  },
  TaskExecutionRegistered: {
    cat: "runtime", layer: LAYERS.runtime,
    task: (f) => ({ taskRef: f.task_ref, status: "Registered" }),
    label: (f) => `注册执行 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `任务 ${T(f)} 注册为组 ${f.group_id ?? "?"} 内的执行单元`,
  },
  TaskExecutionReady: {
    cat: "runtime", layer: LAYERS.runtime,
    task: (f) => ({ taskRef: f.task_ref, status: "Ready" }),
    label: (f) => `就绪 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `DAG 前置满足，任务 ${T(f)} 进入 Ready`,
  },
  TaskExecutionActivated: {
    cat: "runtime", layer: LAYERS.runtime,
    chain: ["chain-decision", "Execute"],
    task: (f) => ({ taskRef: f.task_ref, status: "Active" }),
    label: (f) => `执行 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `任务 ${T(f)} 在组 ${f.group_id ?? "?"} 内激活执行`,
  },
  TaskExecutionCompleted: {
    cat: "runtime", layer: LAYERS.runtime,
    task: (f) => ({ taskRef: f.task_ref, status: "Completed" }),
    label: (f) => `完成 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `任务 ${T(f)} 完成，仅释放该 Task 的临时绑定`,
  },
  TaskExecutionFailed: {
    cat: "recovery", layer: LAYERS.runtime,
    chain: ["chain-recover", "Detect"],
    task: (f) => ({ taskRef: f.task_ref, status: "Failed" }),
    label: (f) => `失败 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `任务 ${T(f)} 进入不可恢复失败`,
  },
  TaskExecutionBindingsReleased: {
    cat: "runtime", layer: LAYERS.runtime,
    label: (f) => `释放任务绑定 ${f.task_ref?.task_id ?? "?"}`,
    desc: (f) => `任务 ${T(f)} 的临时绑定释放：${(f.resource_ids ?? []).join(", ") || "—"}`,
  },
  ContextBindingsReleased: {
    cat: "runtime", layer: LAYERS.runtime,
    label: (f) => `释放 Context 绑定 ${f.context_id ?? "?"}`,
    desc: (f) => `Context ${f.context_id ?? "?"} 的持续资源释放：${(f.resource_ids ?? []).join(", ") || "—"}`,
  },
  MissionActorBound: {
    cat: "group", layer: LAYERS.group,
    chain: ["chain-decision", "Bind"],
    node: (f) => ({ id: f.node_id, state: "hot" }),
    label: (f) => `Actor 绑定 ${f.actor_id ?? "?"}`,
    desc: (f) => `逻辑 Actor ${f.actor_id ?? "?"} 经 Group Bind 获得权威绑定 → 节点 ${f.node_id ?? "?"}`,
  },
  ReconciliationRoleRecoveryRequired: {
    cat: "recovery", layer: LAYERS.group,
    chain: ["chain-recover", "Detect"],
    rung: () => [2, "hot"],
    group: () => ({ effect: "blocked" }),
    label: (f) => `恢复需求 ${f.role_id ?? "?"}`,
    desc: (f) => `对账发现角色 ${f.role_id ?? "?"} 的节点 ${f.node_id ?? "?"} 不再满足资格`,
  },
  RecoveryCandidatesMatched: {
    cat: "recovery", layer: LAYERS.control, station: "match",
    chain: ["chain-recover", "Reconcile"],
    rung: () => [3, "warm"],
    label: (f) => `恢复候选 ${f.role_id ?? "?"}`,
    desc: (f) => `角色 ${f.role_id ?? "?"} 的 role-scoped 候选：${(f.candidate_node_ids ?? []).join(", ") || "空"}`,
  },
  RecoverySchedulingSelected: {
    cat: "recovery", layer: LAYERS.control, station: "schedule",
    chain: ["chain-recover", "Reconcile"],
    rung: () => [3, "warm"],
    label: (f) => `恢复选点 ${f.role_id ?? "?"}`,
    desc: (f) => `调度器为角色 ${f.role_id ?? "?"} 选择替换节点 ${f.replacement_node_id ?? "?"}（原 ${f.previous_node_id ?? "?"}）`,
  },
  RecoverySchedulingNoSelection: {
    cat: "recovery", layer: LAYERS.control, station: "schedule",
    chain: ["chain-recover", "Reconcile"],
    rung: () => [3, "hot"],
    label: (f) => `无恢复候选 ${f.role_id ?? "?"}`,
    desc: (f) => `角色 ${f.role_id ?? "?"} 候选集为空，组保持 RecoveryPending（≠ Failed）`,
  },
  RecoveryAssignmentProposed: {
    cat: "recovery", layer: LAYERS.control, station: "coordinate",
    chain: ["chain-recover", "Reconcile"],
    rung: () => [3, "warm"],
    label: (f) => `恢复提案 ${f.role_id ?? "?"}`,
    desc: (f) => `替换提案：角色 ${f.role_id ?? "?"} → 节点 ${f.replacement_node_id ?? "?"}（未预留资源）`,
  },
  RecoveryAssignmentCommitted: {
    cat: "recovery", layer: LAYERS.control, station: "coordinate",
    chain: ["chain-recover", "Adapt"],
    rung: () => [3, "warm"],
    label: (f) => `恢复提交 ${f.role_id ?? "?"}`,
    desc: (f) => `原子建立替换承诺：角色 ${f.role_id ?? "?"} → 节点 ${f.replacement_node_id ?? "?"}`,
  },
  RecoveryAssignmentAborted: {
    cat: "recovery", layer: LAYERS.control, station: "coordinate",
    chain: ["chain-recover", "Reconcile"],
    rung: () => [3, "warm"],
    label: (f) => `恢复放弃 ${f.role_id ?? "?"}`,
    desc: (f) => `放弃未绑定的替换承诺并释放替换资源（可重新 Match）`,
  },
  RecoveryRebound: {
    cat: "recovery", layer: LAYERS.group,
    chain: ["chain-recover", "Adapt"],
    rung: () => [2, "warm"],
    group: () => ({ effect: "active" }),
    node: (f) => ({ id: f.to_node, state: "hot" }),
    label: (f) => `重绑定 ${f.role_id ?? "?"}`,
    desc: (f) => `角色 ${f.role_id ?? "?"}: ${f.from_node ?? "?"} → ${f.to_node ?? "?"}，组经 Adapted → Active`,
  },
  RuntimeExecutionRecoveryRequired: {
    cat: "recovery", layer: LAYERS.runtime,
    chain: ["chain-recover", "Detect"],
    rung: () => [1, "hot"],
    label: (f) => `执行歧义 ${f.execution_id ?? "?"}`,
    desc: (f) => `Runtime 无法安全继续执行 ${f.execution_id ?? "?"}：${f.reason ?? "?"}`,
  },
  ExecutionRelationRegistered: {
    cat: "runtime", layer: LAYERS.runtime,
    label: (f) => `注册关系 ${f.relation_id ?? "?"}`,
    desc: (f) => `执行关系 ${f.relation_id ?? "?"}（${f.kind ?? "?"}）：${f.source_task_ref?.task_id ?? "?"} → ${f.target_task_ref?.task_id ?? "?"}`,
  },
  ExecutionRelationStateChanged: {
    cat: "runtime", layer: LAYERS.runtime,
    label: (f) => `关系 ${f.relation_id ?? "?"}: ${f.previous ?? "?"} → ${f.current ?? "?"}`,
    desc: (f) => `执行关系 ${f.relation_id ?? "?"} 状态归约为 ${f.current ?? "?"}`,
  },
  ExecutionRelationReconciliationRequired: {
    cat: "recovery", layer: LAYERS.runtime,
    chain: ["chain-recover", "Detect"],
    rung: () => [1, "hot"],
    label: (f) => `关系违例 ${f.relation_id ?? "?"}`,
    desc: (f) => `关系 ${f.relation_id ?? "?"} 处于 ${f.state ?? "?"}，fence 目标任务推进：${f.reason ?? "?"}`,
  },
  PeerChannelReadinessObserved: {
    cat: "runtime", layer: LAYERS.runtime,
    node: (f) => ({ id: f.node_id, state: "hot" }),
    label: (f) => `peer 通道 ${f.ready === false ? "失效" : "就绪"} ${f.context_role_id ?? "?"}`,
    desc: (f) => `ContextRole ${f.context_role_id ?? "?"} @ ${f.node_id ?? "?"} 通道 ${f.channel_instance_id ?? "?"} ${f.ready === false ? "readiness 失效" : "确认就绪"}`,
  },
  StateRecordObserved: {
    cat: "observe", layer: LAYERS.smp,
    chain: ["chain-observe", "Update"],
    label: () => `State 记录入账`,
    desc: () => `source-aware State 记录被接受并进入投影`,
  },
  MemoryManifestPublished: {
    cat: "observe", layer: LAYERS.smp,
    chain: ["chain-observe", "Update"],
    label: () => `Memory 发布`,
    desc: (f) => `不可变 Memory revision 可被发现（bytes 留在 Artifact CAS）`,
  },
  MemoryArtifactStaged: {
    cat: "observe", layer: LAYERS.smp,
    chain: ["chain-observe", "Update"],
    label: () => `Memory 暂存`,
    desc: (f) => `节点 ${f.node_id ?? "?"} digest 校验后暂存 Memory revision`,
  },
  MemoryArtifactImported: {
    cat: "observe", layer: LAYERS.smp,
    chain: ["chain-observe", "Update"],
    label: () => `Memory 导入`,
    desc: (f) => `节点 ${f.node_id ?? "?"} 的 provider 导入 Memory revision`,
  },
  MemoryArtifactRejected: {
    cat: "observe", layer: LAYERS.smp,
    label: () => `Memory 拒绝`,
    desc: (f) => `节点 ${f.node_id ?? "?"} 拒绝 Memory 交换：${f.reason ?? "?"}`,
  },
  MapArtifactDeclared: {
    cat: "observe", layer: LAYERS.smp,
    label: () => `地图声明`,
    desc: () => `地图 revision manifest 在 bytes 发布前声明`,
  },
  MapArtifactPublished: {
    cat: "observe", layer: LAYERS.smp,
    label: () => `地图发布`,
    desc: () => `不可变地图 Artifact 在中心 CAS 可用`,
  },
  MapArtifactStaged: {
    cat: "observe", layer: LAYERS.smp,
    label: () => `地图暂存`,
    desc: (f) => `节点 ${f.node_id ?? "?"} 开始暂存地图 revision`,
  },
  MapArtifactImported: {
    cat: "observe", layer: LAYERS.smp,
    label: () => `地图导入`,
    desc: (f) => `节点 ${f.node_id ?? "?"} 将地图 revision 导入本地受控缓存`,
  },
  MapLocalizationVerified: {
    cat: "observe", layer: LAYERS.smp,
    chain: ["chain-observe", "Update"],
    label: () => `定位验证`,
    desc: (f) => `节点 ${f.node_id ?? "?"} 验证地图与 anchor ${f.anchor_id ?? "?"}`,
  },
  MapLocalizationEvidenceRecorded: {
    cat: "observe", layer: LAYERS.smp,
    chain: ["chain-observe", "Update"],
    label: () => `定位证据`,
    desc: () => `完整强定位验证证据被记录（绑定 artifact 与 execution 身份）`,
  },
  MapArtifactRejected: {
    cat: "observe", layer: LAYERS.smp,
    label: () => `地图拒绝`,
    desc: (f) => `节点 ${f.node_id ?? "?"} 拒绝地图 artifact：${f.reason ?? "?"}`,
  },
  NodeObservation: {
    cat: "observe", layer: LAYERS.nodes,
    chain: ["chain-observe", "Observe"],
    node: (f) => ({ id: f.TaskCompleted?.node_id ?? f.TaskStarted?.node_id, state: "hot" }),
    label: (f) => {
      const inner = f.TaskCompleted ?? f.TaskStarted ?? {};
      return `节点观测 ${inner.task_ref?.task_id ?? ""}`.trim();
    },
    desc: (f) => {
      const inner = f.TaskCompleted ?? f.TaskStarted ?? {};
      if (!inner.node_id) return `节点发出执行观测`;
      return `节点 ${inner.node_id} 报告任务 ${inner.task_ref?.task_id ?? "?"} 的 ${f.TaskCompleted ? "完成" : "生命周期"}事实`;
    },
  },
};

/**
 * Fallback catalog entry for variants this console does not model explicitly;
 * `layer: null` keeps unknown variants silent on the stage (ticker only).
 * @type {object}
 */
export const FALLBACK_ENTRY = {
  cat: "system",
  layer: null,
  label: () => "域事件",
  desc: () => "领域证据事件（未建模的可视化动作）",
};

/**
 * Resolves the catalog entry for one normalized event kind.
 * @param {string} kind - Externally tagged payload variant name.
 * @returns {object} Catalog entry (fallback for unknown variants).
 */
export function entryFor(kind) {
  return EVENT_CATALOG[kind] ?? FALLBACK_ENTRY;
}
