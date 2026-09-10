/**
 * Built-in demo traces for offline console playback.
 *
 * Every payload mirrors the exact serde shape of `core/domain::EventPayload`
 * (externally tagged; identifiers as plain strings; `task_ref` as
 * `{mission_id, task_id}`), so the console pipeline cannot distinguish a demo
 * event from a live `/v1/events` record. The demo plan is the real
 * `scenarios/phase1-mission-v0.3/mission-plan.json` artifact.
 */

/** Demo node fleet (mirrors `/v1/inventory` projections). */
const DEMO_NODES = [
  { id: "node-dog-a", kindLabel: "robot · transport", health: "Healthy", liveness: "Reachable", resourcesLabel: "space: corridor-a ×1", state: "registered" },
  { id: "node-dog-b", kindLabel: "robot · transport (standby)", health: "Healthy", liveness: "Reachable", resourcesLabel: "space: corridor-b ×1", state: "registered" },
  { id: "node-edge-1", kindLabel: "edge · compute/observation", health: "Healthy", liveness: "Reachable", resourcesLabel: "compute: gpu-edge ×8", state: "registered" },
];

/** The phase1 mission plan (cross-node delivery with continuity context). */
const PHASE1_PLAN = {
  schema_version: "roboguide.mission-plan/v0.3",
  mission: {
    id: "mission-phase1-001",
    objective: "使用持续运输角色和边缘算力完成可恢复的跨节点交付",
  },
  contexts: [{
    id: "delivery-context",
    roles: [
      { id: "carrier", actor: "carrier" },
      { id: "edge", actor: "edge" },
    ],
    relations: [],
  }],
  tasks: [
    { id: "prepare", description: "边缘节点准备交付计划", depends_on: [] },
    { id: "stage", description: "运输节点携带共享上下文到交接点", depends_on: ["prepare"] },
    { id: "deliver", description: "同一持续运输角色完成最终交付", depends_on: ["stage"] },
    { id: "verify", description: "边缘节点验证交付结果", depends_on: ["deliver"] },
  ],
};

const MISSION = "mission-phase1-001";
const GROUP = "group-mission-phase1-001";
const ref = (task) => ({ mission_id: MISSION, task_id: task });

/**
 * Shared event builders for the decision pipeline of one task.
 * @param {string} task - Task id.
 * @param {Array<{role_id: string, node_id: string, resource_ids: string[]}>} assignments - Selections.
 * @param {number} t0 - Interval start (ms).
 * @param {number} t1 - Interval end (ms).
 * @returns {Array<object>} Payload list covering Match→Schedule→Propose→Commit→Bind.
 */
function pipeline(task, assignments, t0, t1) {
  return [
    { CandidatesMatched: { task_ref: ref(task) } },
    { TaskSchedulingSelected: { task_ref: ref(task), assignments } },
    { SchedulingReservationCreated: { group_id: GROUP, task_ref: ref(task), starts_at: t0, ends_at: t1, snapshot_version: 7 } },
    { ProposalCreated: { task_ref: ref(task) } },
    { PlanCommitted: { task_ref: ref(task) } },
    { ExecutionGroupBound: { group_id: GROUP, task_ref: ref(task) } },
  ];
}

/**
 * Build the happy-path trace: all four tasks complete, group released.
 * @returns {Array<{delay: number, payload: object}>} Timed payloads.
 */
function happyTrace() {
  const t = (ms) => 1700000000000 + ms;
  const steps = [];
  const add = (delay, payload) => steps.push({ delay, payload });

  add(200, { NodeRegistered: { node_id: "node-dog-a", lease_id: "lease-a-1" } });
  add(350, { NodeRegistered: { node_id: "node-dog-b", lease_id: "lease-b-1" } });
  add(500, { NodeRegistered: { node_id: "node-edge-1", lease_id: "lease-e-1" } });
  add(600, { StateRecordObserved: { record: { object_class: "node", semantic: "reported", source: "node:node-dog-a/registration" } } });
  add(650, { NodeHeartbeatAccepted: { node_id: "node-dog-a", lease_id: "lease-a-1" } });
  add(700, { NodeHeartbeatAccepted: { node_id: "node-edge-1", lease_id: "lease-e-1" } });

  // Mission accepted (UI submits the plan itself) → group + registrations.
  add(900, { ExecutionGroupCreated: { group_id: GROUP, mission_id: MISSION } });
  for (const task of ["prepare", "stage", "deliver", "verify"]) {
    add(60, { TaskExecutionRegistered: { group_id: GROUP, task_ref: ref(task), context_id: "delivery-context" } });
  }
  add(120, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("prepare") } });

  // prepare @ edge-1.
  pipeline("prepare", [{ role_id: "prepare-compute", node_id: "node-edge-1", resource_ids: ["gpu-edge-1"] }], t(10), t(60))
    .forEach((payload, i) => add(160 + i * 150, payload));
  add(1150, { MissionActorBound: { mission_id: MISSION, actor_id: "edge", node_id: "node-edge-1", task_ref: ref("prepare"), group_id: GROUP } });
  add(1300, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("prepare") } });
  add(1500, { StateRecordObserved: { record: { object_class: "node", semantic: "reported", source: "node:node-edge-1/health" } } });
  add(2100, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("prepare") } });
  add(2160, { TaskExecutionBindingsReleased: { group_id: GROUP, task_ref: ref("prepare"), resource_ids: ["gpu-edge-1"] } });

  // stage @ dog-a (context-scoped corridor binding persists across tasks).
  add(300, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("stage") } });
  pipeline("stage", [{ role_id: "stage-carrier", node_id: "node-dog-a", resource_ids: ["corridor-a"] }], t(80), t(160))
    .forEach((payload, i) => add(200 + i * 150, payload));
  add(1100, { MissionActorBound: { mission_id: MISSION, actor_id: "carrier", node_id: "node-dog-a", task_ref: ref("stage"), group_id: GROUP } });
  add(1250, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("stage") } });
  add(1500, { NodeHeartbeatAccepted: { node_id: "node-dog-a", lease_id: "lease-a-1" } });
  add(2400, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("stage") } });

  // deliver @ dog-a with a first-pass deferral (shows deferral ≠ failure).
  add(300, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("deliver") } });
  add(160, { CandidatesMatched: { task_ref: ref("deliver") } });
  add(180, { TaskSchedulingDeferred: { task_ref: ref("deliver"), reason: "NoFeasibleStartInWindow" } });
  add(600, { CandidatesMatched: { task_ref: ref("deliver") } });
  [{ TaskSchedulingSelected: { task_ref: ref("deliver"), assignments: [{ role_id: "deliver-carrier", node_id: "node-dog-a", resource_ids: ["corridor-a"] }] } },
   { SchedulingReservationCreated: { group_id: GROUP, task_ref: ref("deliver"), starts_at: t(170), ends_at: t(260), snapshot_version: 9 } },
   { ProposalCreated: { task_ref: ref("deliver") } },
   { PlanCommitted: { task_ref: ref("deliver") } },
   { ExecutionGroupBound: { group_id: GROUP, task_ref: ref("deliver") } },
  ].forEach((payload, i) => add(200 + i * 150, payload));
  add(1050, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("deliver") } });
  add(2200, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("deliver") } });

  // verify @ edge-1.
  add(300, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("verify") } });
  pipeline("verify", [{ role_id: "verify-compute", node_id: "node-edge-1", resource_ids: ["gpu-edge-1"] }], t(280), t(340))
    .forEach((payload, i) => add(200 + i * 150, payload));
  add(1050, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("verify") } });
  add(1900, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("verify") } });
  add(220, { TaskExecutionBindingsReleased: { group_id: GROUP, task_ref: ref("verify"), resource_ids: ["gpu-edge-1"] } });
  add(220, { ContextBindingsReleased: { group_id: GROUP, context_id: "delivery-context", resource_ids: ["corridor-a"] } });
  add(300, { ExecutionGroupCompleted: { group_id: GROUP, task_ref: ref("verify") } });
  add(500, { ExecutionGroupReleased: { group_id: GROUP, task_ref: ref("verify"), resource_ids: ["corridor-a", "gpu-edge-1"] } });
  return steps;
}

/**
 * Build the failure-recovery trace: dog-a lease expires mid-stage; the carrier
 * role is re-matched, proposed, committed, and rebound onto dog-b.
 * @returns {Array<{delay: number, payload: object}>} Timed payloads.
 */
function recoveryTrace() {
  const t = (ms) => 1700000200000 + ms;
  const steps = [];
  const add = (delay, payload) => steps.push({ delay, payload });

  add(200, { NodeRegistered: { node_id: "node-dog-a", lease_id: "lease-a-2" } });
  add(300, { NodeRegistered: { node_id: "node-dog-b", lease_id: "lease-b-2" } });
  add(400, { NodeRegistered: { node_id: "node-edge-1", lease_id: "lease-e-2" } });

  add(700, { ExecutionGroupCreated: { group_id: GROUP, mission_id: MISSION } });
  for (const task of ["prepare", "stage", "deliver", "verify"]) {
    add(60, { TaskExecutionRegistered: { group_id: GROUP, task_ref: ref(task), context_id: "delivery-context" } });
  }
  add(120, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("prepare") } });
  pipeline("prepare", [{ role_id: "prepare-compute", node_id: "node-edge-1", resource_ids: ["gpu-edge-1"] }], t(10), t(60))
    .forEach((payload, i) => add(160 + i * 140, payload));
  add(1000, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("prepare") } });
  add(1500, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("prepare") } });

  add(300, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("stage") } });
  pipeline("stage", [{ role_id: "stage-carrier", node_id: "node-dog-a", resource_ids: ["corridor-a"] }], t(80), t(160))
    .forEach((payload, i) => add(200 + i * 140, payload));
  add(1000, { MissionActorBound: { mission_id: MISSION, actor_id: "carrier", node_id: "node-dog-a", task_ref: ref("stage"), group_id: GROUP } });
  add(1150, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("stage") } });

  // --- failure mid-flight ---
  add(1600, { NodeLeaseExpired: { node_id: "node-dog-a", lease_id: "lease-a-2" } });
  add(500, { ReconciliationRoleRecoveryRequired: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", node_id: "node-dog-a" } });
  add(400, { ExecutionGroupBlocked: { group_id: GROUP, task_ref: ref("stage"), reason: "assigned node node-dog-a no longer eligible for role stage-carrier" } });
  add(400, { ExecutionGroupRoleBindingReleased: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", node_id: "node-dog-a", resource_ids: ["corridor-a"] } });
  add(500, { RecoveryCandidatesMatched: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", candidate_node_ids: ["node-dog-b"] } });
  add(400, { RecoverySchedulingSelected: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", previous_node_id: "node-dog-a", replacement_node_id: "node-dog-b", resource_ids: ["corridor-b"] } });
  add(400, { RecoveryAssignmentProposed: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", replacement_node_id: "node-dog-b", resource_ids: ["corridor-b"] } });
  add(500, { RecoveryAssignmentCommitted: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", replacement_node_id: "node-dog-b", resource_ids: ["corridor-b"] } });
  add(500, { RecoveryRebound: { group_id: GROUP, task_ref: ref("stage"), role_id: "stage-carrier", from_node: "node-dog-a", to_node: "node-dog-b" } });
  add(300, { NodeHeartbeatAccepted: { node_id: "node-dog-b", lease_id: "lease-b-2" } });
  add(400, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("stage") } });
  add(1800, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("stage") } });

  // deliver on the rebound carrier.
  add(300, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("deliver") } });
  pipeline("deliver", [{ role_id: "deliver-carrier", node_id: "node-dog-b", resource_ids: ["corridor-b"] }], t(200), t(280))
    .forEach((payload, i) => add(200 + i * 140, payload));
  add(900, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("deliver") } });
  add(1600, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("deliver") } });

  add(300, { TaskExecutionReady: { group_id: GROUP, task_ref: ref("verify") } });
  pipeline("verify", [{ role_id: "verify-compute", node_id: "node-edge-1", resource_ids: ["gpu-edge-1"] }], t(300), t(360))
    .forEach((payload, i) => add(200 + i * 140, payload));
  add(900, { TaskExecutionActivated: { group_id: GROUP, task_ref: ref("verify") } });
  add(1500, { TaskExecutionCompleted: { group_id: GROUP, task_ref: ref("verify") } });
  add(200, { ContextBindingsReleased: { group_id: GROUP, context_id: "delivery-context", resource_ids: ["corridor-b"] } });
  add(250, { ExecutionGroupCompleted: { group_id: GROUP, task_ref: ref("verify") } });
  add(450, { ExecutionGroupReleased: { group_id: GROUP, task_ref: ref("verify"), resource_ids: ["corridor-b", "gpu-edge-1"] } });
  return steps;
}

/** Cluster demo fleet: six heterogeneous devices across four node kinds. */
const CLUSTER_NODES = [
  { id: "node-dog-a", kindLabel: "robot · mapping/transport", health: "Healthy", liveness: "Reachable", resourcesLabel: "space: corridor-a ×1", state: "registered" },
  { id: "node-dog-b", kindLabel: "robot · transport (standby)", health: "Healthy", liveness: "Reachable", resourcesLabel: "space: corridor-b ×1", state: "registered" },
  { id: "node-edge-1", kindLabel: "edge · compute", health: "Healthy", liveness: "Reachable", resourcesLabel: "compute: gpu-edge ×8", state: "registered" },
  { id: "node-cam-1", kindLabel: "perception · camera", health: "Healthy", liveness: "Reachable", resourcesLabel: "obs: stream-cam ×1", state: "registered" },
  { id: "node-pad-1", kindLabel: "interaction · HMI pad", health: "Healthy", liveness: "Reachable", resourcesLabel: "hmi: console ×1", state: "registered" },
  { id: "node-dock-1", kindLabel: "infrastructure · charging dock", health: "Healthy", liveness: "Reachable", resourcesLabel: "dock: slot ×1", state: "registered" },
];

/** Mission A: dog-a builds a map, edge rebuilds/publishes it. */
const PLAN_MAP_A = {
  mission: { id: "mission-map-a", objective: "A 狗建图并发布共享地图" },
  tasks: [
    { id: "scan", description: "A 狗扫描区域建图", depends_on: [] },
    { id: "publish", description: "边缘重建并发布地图", depends_on: ["scan"] },
  ],
};

/** Mission B (concurrent): camera observes, pad reports. */
const PLAN_MONITOR = {
  mission: { id: "mission-monitor", objective: "区域感知监控与上报" },
  tasks: [
    { id: "observe", description: "摄像头持续观测", depends_on: [] },
    { id: "report", description: "平板聚合上报", depends_on: ["observe"] },
  ],
};

/** Mission C (after publish): dog-b imports the map, verifies, delivers. */
const PLAN_MAP_B = {
  mission: { id: "mission-map-b", objective: "B 狗导入地图并完成配送" },
  tasks: [
    { id: "fetch-map", description: "B 狗拉取并导入地图", depends_on: [] },
    { id: "localize", description: "强定位验证", depends_on: ["fetch-map"] },
    { id: "deliver", description: "基于共享地图配送", depends_on: ["localize"] },
  ],
};

const MAP_ID = "map-warehouse-r1";

/**
 * Build the cluster trace: three concurrent Missions over six devices with the
 * full Spatial Memory pipeline (declare → publish → stage → import →
 * localization evidence) shared between the two dogs.
 * @returns {Array<{delay: number, payload: object}>} Timed payloads.
 */
function clusterTrace() {
  const steps = [];
  const add = (delay, payload) => steps.push({ delay, payload });
  const GA = "group-mission-map-a";
  const GM = "group-mission-monitor";
  const GB = "group-mission-map-b";
  const ref = (mission, task) => ({ mission_id: mission, task_id: task });
  const MA = "mission-map-a";
  const MO = "mission-monitor";
  const MB = "mission-map-b";

  const pipeline = (mission, group, task, assignments, t0, t1) => [
    { CandidatesMatched: { task_ref: ref(mission, task) } },
    { TaskSchedulingSelected: { task_ref: ref(mission, task), assignments } },
    { SchedulingReservationCreated: { group_id: group, task_ref: ref(mission, task), starts_at: t0, ends_at: t1, snapshot_version: 11 } },
    { ProposalCreated: { task_ref: ref(mission, task) } },
    { PlanCommitted: { task_ref: ref(mission, task) } },
    { ExecutionGroupBound: { group_id: group, task_ref: ref(mission, task) } },
  ];
  const t = (ms) => 1700000500000 + ms;

  // Phase 0 — the whole cluster comes online.
  for (const [i, node] of CLUSTER_NODES.entries()) {
    add(160, { NodeRegistered: { node_id: node.id, lease_id: `lease-${i + 1}` } });
  }
  for (const node of CLUSTER_NODES) {
    add(90, { NodeHeartbeatAccepted: { node_id: node.id, lease_id: "lease-1" } });
  }
  add(200, { StateRecordObserved: { record: { object_class: "node", semantic: "reported", source: "node:node-dog-a/health" } } });
  add(120, { StateRecordObserved: { record: { object_class: "node", semantic: "reported", source: "node:node-edge-1/health" } } });

  // Phase 1 — map-a and monitor accepted concurrently.
  add(500, { ExecutionGroupCreated: { group_id: GA, mission_id: MA } });
  add(80, { TaskExecutionRegistered: { group_id: GA, task_ref: ref(MA, "scan"), context_id: "mapping-context" } });
  add(60, { TaskExecutionRegistered: { group_id: GA, task_ref: ref(MA, "publish"), context_id: "mapping-context" } });
  add(240, { ExecutionGroupCreated: { group_id: GM, mission_id: MO } });
  add(80, { TaskExecutionRegistered: { group_id: GM, task_ref: ref(MO, "observe"), context_id: "monitor-context" } });
  add(60, { TaskExecutionRegistered: { group_id: GM, task_ref: ref(MO, "report"), context_id: "monitor-context" } });
  add(200, { TaskExecutionReady: { group_id: GA, task_ref: ref(MA, "scan") } });
  pipeline(MA, GA, "scan", [{ role_id: "mapper", node_id: "node-dog-a", resource_ids: ["corridor-a"] }], t(10), t(90))
    .forEach((payload, i) => add(140 + i * 130, payload));
  add(950, { MissionActorBound: { mission_id: MA, actor_id: "mapper-dog", node_id: "node-dog-a", task_ref: ref(MA, "scan"), group_id: GA } });
  add(500, { TaskExecutionActivated: { group_id: GA, task_ref: ref(MA, "scan") } });

  // Monitor observes while dog-a scans.
  add(300, { TaskExecutionReady: { group_id: GM, task_ref: ref(MO, "observe") } });
  pipeline(MO, GM, "observe", [{ role_id: "watcher", node_id: "node-cam-1", resource_ids: ["stream-cam"] }], t(20), t(400))
    .forEach((payload, i) => add(160 + i * 130, payload));
  add(950, { TaskExecutionActivated: { group_id: GM, task_ref: ref(MO, "observe") } });
  add(700, { NodeObservation: { TaskStarted: { node_id: "node-cam-1", role_id: "watcher", task_ref: ref(MO, "observe") } } });

  // map-a scan completes; edge rebuilds and publishes.
  add(1200, { TaskExecutionCompleted: { group_id: GA, task_ref: ref(MA, "scan") } });
  add(240, { TaskExecutionReady: { group_id: GA, task_ref: ref(MA, "publish") } });
  pipeline(MA, GA, "publish", [{ role_id: "builder", node_id: "node-edge-1", resource_ids: ["gpu-edge"] }], t(110), t(190))
    .forEach((payload, i) => add(140 + i * 130, payload));
  add(950, { TaskExecutionActivated: { group_id: GA, task_ref: ref(MA, "publish") } });
  add(1400, { TaskExecutionCompleted: { group_id: GA, task_ref: ref(MA, "publish") } });
  add(200, { TaskExecutionBindingsReleased: { group_id: GA, task_ref: ref(MA, "publish"), resource_ids: ["gpu-edge"] } });
  add(300, { MapArtifactDeclared: { manifest: { map_id: MAP_ID, revision: "r1" } } });
  add(400, { MapArtifactPublished: { manifest: { map_id: MAP_ID, revision: "r1" } } });
  add(200, { StateRecordObserved: { record: { object_class: "world", semantic: "observed", source: "artifact:map-warehouse-r1" } } });

  // Phase 2 — map-b starts only after the map is published (selective import).
  add(600, { ExecutionGroupCreated: { group_id: GB, mission_id: MB } });
  for (const task of ["fetch-map", "localize", "deliver"]) {
    add(70, { TaskExecutionRegistered: { group_id: GB, task_ref: ref(MB, task), context_id: "delivery-b-context" } });
  }
  add(200, { TaskExecutionReady: { group_id: GB, task_ref: ref(MB, "fetch-map") } });
  pipeline(MB, GB, "fetch-map", [{ role_id: "importer", node_id: "node-dog-b", resource_ids: ["corridor-b"] }], t(210), t(280))
    .forEach((payload, i) => add(140 + i * 130, payload));
  add(950, { TaskExecutionActivated: { group_id: GB, task_ref: ref(MB, "fetch-map") } });
  add(700, { MapArtifactStaged: { manifest: { map_id: MAP_ID, revision: "r1" }, node_id: "node-dog-b", mission_id: MB } });
  add(600, { MapArtifactImported: { manifest: { map_id: MAP_ID, revision: "r1" }, node_id: "node-dog-b", mission_id: MB } });
  add(500, { TaskExecutionCompleted: { group_id: GB, task_ref: ref(MB, "fetch-map") } });

  // localize on dog-b with strong localization evidence.
  add(260, { TaskExecutionReady: { group_id: GB, task_ref: ref(MB, "localize") } });
  pipeline(MB, GB, "localize", [{ role_id: "localizer", node_id: "node-dog-b", resource_ids: [] }], t(300), t(360))
    .forEach((payload, i) => add(140 + i * 130, payload));
  add(950, { TaskExecutionActivated: { group_id: GB, task_ref: ref(MB, "localize") } });
  add(800, { MapLocalizationVerified: { artifact: { map_id: MAP_ID, revision: "r1" }, node_id: "node-dog-b", mission_id: MB, anchor_id: "anchor-dock-1" } });
  add(300, { MapLocalizationEvidenceRecorded: { evidence: { map_id: MAP_ID, node_id: "node-dog-b", frame: "map" } } });
  add(500, { TaskExecutionCompleted: { group_id: GB, task_ref: ref(MB, "localize") } });

  // monitor completes while dog-b delivers.
  add(400, { TaskExecutionCompleted: { group_id: GM, task_ref: ref(MO, "observe") } });
  add(200, { TaskExecutionReady: { group_id: GM, task_ref: ref(MO, "report") } });
  pipeline(MO, GM, "report", [{ role_id: "reporter", node_id: "node-pad-1", resource_ids: ["console"] }], t(420), t(500))
    .forEach((payload, i) => add(140 + i * 130, payload));
  add(950, { TaskExecutionActivated: { group_id: GM, task_ref: ref(MO, "report") } });
  add(1100, { TaskExecutionCompleted: { group_id: GM, task_ref: ref(MO, "report") } });
  add(200, { ContextBindingsReleased: { group_id: GM, context_id: "monitor-context", resource_ids: ["stream-cam"] } });
  add(300, { ExecutionGroupCompleted: { group_id: GM, task_ref: ref(MO, "report") } });
  add(400, { ExecutionGroupReleased: { group_id: GM, task_ref: ref(MO, "report"), resource_ids: ["console"] } });

  add(260, { TaskExecutionReady: { group_id: GB, task_ref: ref(MB, "deliver") } });
  pipeline(MB, GB, "deliver", [{ role_id: "carrier-b", node_id: "node-dog-b", resource_ids: ["corridor-b"] }], t(380), t(520))
    .forEach((payload, i) => add(140 + i * 130, payload));
  add(950, { TaskExecutionActivated: { group_id: GB, task_ref: ref(MB, "deliver") } });
  add(700, { NodeHeartbeatAccepted: { node_id: "node-dog-b", lease_id: "lease-2" } });
  add(1600, { TaskExecutionCompleted: { group_id: GB, task_ref: ref(MB, "deliver") } });
  add(200, { ContextBindingsReleased: { group_id: GB, context_id: "delivery-b-context", resource_ids: ["corridor-b"] } });
  add(300, { ExecutionGroupCompleted: { group_id: GB, task_ref: ref(MB, "deliver") } });
  add(450, { ExecutionGroupReleased: { group_id: GB, task_ref: ref(MB, "deliver"), resource_ids: ["corridor-b"] } });

  add(400, { ExecutionGroupCompleted: { group_id: GA, task_ref: ref(MA, "publish") } });
  add(500, { ExecutionGroupReleased: { group_id: GA, task_ref: ref(MA, "publish"), resource_ids: ["corridor-a"] } });
  return steps;
}

/** Demo scenario descriptors surfaced in the scenario selector. */
export const DEMOS = [
  {
    id: "demo-cluster",
    title: "演示 · 集群双狗地图共享（3 Mission 并发）",
    nodes: CLUSTER_NODES,
    missions: [PLAN_MAP_A, PLAN_MONITOR, PLAN_MAP_B],
    build: clusterTrace,
  },
  {
    id: "demo-happy",
    title: "演示 · 跨节点交付（完整闭环）",
    plan: PHASE1_PLAN,
    nodes: DEMO_NODES,
    build: happyTrace,
  },
  {
    id: "demo-recovery",
    title: "演示 · 节点故障 → 恢复重绑定",
    plan: PHASE1_PLAN,
    nodes: DEMO_NODES,
    build: recoveryTrace,
  },
];

/** Playback pace factor; authored gaps are compressed for a snappier demo. */
const PACE = 0.55;

/**
 * Expands one demo trace into normalized console events.
 *
 * Authored `delay` values are inter-event gaps; they are accumulated into
 * absolute offsets so event ordering matches domain causality.
 *
 * @param {object} demo - Demo descriptor from {@link DEMOS}.
 * @returns {{events: object[], totalMs: number}} Events with absolute delays.
 */
export function buildDemoEvents(demo) {
  let sequence = 1;
  let running = 0;
  const base = Date.now();
  const events = demo.build().map(({ delay, payload }) => {
    running += delay * PACE;
    return {
      sequence: sequence++,
      event_id: `demo-${sequence}`,
      timestamp_ms: base + running,
      correlation_id: `demo-${demo.id}`,
      causation_id: null,
      payload_schema: "roboguide.domain-event/v1",
      payload,
      delay: running,
    };
  });
  return { events, totalMs: running };
}
