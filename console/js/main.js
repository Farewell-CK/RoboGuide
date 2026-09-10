/**
 * Console composition root.
 *
 * Owns console state (per-Mission plans, lifecycles, task maps, cluster nodes),
 * drives the stage and panels from normalized domain events, and provides two
 * ingestion modes: built-in demo replay and live polling of the real
 * `/v1/events` evidence log. Multi-Mission is a first-class concept: every
 * mission-scoped event is routed to its owning Mission record, the stage shows
 * one Execution Group frame per active Mission, and the mission list selects
 * which journey the DAG panel focuses on. The console is an observer: it never
 * mutates RoboGuide semantics except by calling the existing Mission
 * submit/cancel HTTP endpoints.
 */

import { CHAINS, MISSION_PALETTE, decodeEvent, entryFor, missionIdOf, taskKey } from "./model.js";
import { Stage } from "./stage.js";
import {
  MissionPanel, DagPanel, CalendarPanel, RecoveryPanel, StatePanel, StatsPanel, Ticker,
} from "./panels.js";
import { DEMOS, buildDemoEvents } from "./replay.js";
import { Api } from "./api.js";

const api = new Api();

const els = {
  modeDemo: document.getElementById("mode-demo"),
  modeLive: document.getElementById("mode-live"),
  scenario: document.getElementById("scenario-select"),
  run: document.getElementById("run-btn"),
  cancel: document.getElementById("cancel-btn"),
  reset: document.getElementById("reset-btn"),
  conn: document.getElementById("connection"),
  connText: document.getElementById("conn-text"),
  mission: document.getElementById("mission-card"),
  dag: document.getElementById("dag-view"),
  calendar: document.getElementById("calendar-view"),
  state: document.getElementById("state-view"),
  recovery: document.getElementById("recovery-view"),
  stats: document.getElementById("stats-view"),
  tickerList: document.getElementById("ticker-list"),
  tickerFilters: document.getElementById("ticker-filters"),
  modal: document.getElementById("payload-modal"),
  modalTitle: document.getElementById("modal-title"),
  modalJson: document.getElementById("modal-json"),
  modalClose: document.getElementById("modal-close"),
};

const stage = new Stage(document.getElementById("stage"));
const missionPanel = new MissionPanel(els.mission, (id) => setFocus(id));
const dagPanel = new DagPanel(els.dag);
const calendarPanel = new CalendarPanel(els.calendar);
const recoveryPanel = new RecoveryPanel(els.recovery);
const statePanel = new StatePanel(els.state);
const statsPanel = new StatsPanel(els.stats);
const ticker = new Ticker(els.tickerList, els.tickerFilters, inspect);

/** Live scenario plan files shipped with the console. */
const LIVE_SCENARIOS = [
  "phase1-mission-v0.3.json",
  "phase1-mission-v0.2.json",
  "execution-relations-v0.1.json",
  "distributed-spatial-memory-a.json",
];

const TERMINAL_STATUSES = new Set(["Completed", "Failed", "Cancelled"]);

/** Console application state; `missions` is the single source of truth. */
const state = {
  mode: "demo",
  missions: new Map(),
  focus: null,
  nodes: new Map(),
  nodeMissions: new Map(),
  timers: [],
  pollTimer: null,
  inventoryTimer: null,
  missionTimer: null,
  lastSequence: null,
  chainState: new Map(),
  activeExecutions: 0,
  polling: false,
};

/** Builds the semantic chain step chips once. */
for (const chain of CHAINS) {
  const bar = document.getElementById(chain.id).querySelector(".chain-steps");
  bar.style.setProperty("--chain-color", chain.color);
  for (const step of chain.steps) {
    const chip = document.createElement("span");
    chip.className = "chain-step";
    chip.textContent = step;
    chip.dataset.step = step;
    if (chain.dormant.includes(step)) chip.title = "架构预留：当前代码未实现该阶段";
    bar.appendChild(chip);
  }
}

/**
 * Lights one chain step, marking earlier steps done.
 * @param {string} chainId - Chain element id.
 * @param {string} step - Step name.
 */
function lightChain(chainId, step) {
  const chain = CHAINS.find((c) => c.id === chainId);
  if (!chain) return;
  const index = chain.steps.indexOf(step);
  if (index < 0) return;
  const bar = document.getElementById(chainId).querySelector(".chain-steps");
  bar.style.setProperty("--chain-color", chain.color);
  chain.steps.forEach((name, i) => {
    const chip = bar.children[i];
    chip.classList.remove("lit", "done");
    if (i < index) chip.classList.add("done");
    if (i === index) chip.classList.add("lit");
  });
  state.chainState.set(chainId, Math.max(state.chainState.get(chainId) ?? -1, index));
}

/** Clears chain lighting for a fresh mission. */
function resetChains() {
  state.chainState = new Map();
  for (const chain of CHAINS) {
    const bar = document.getElementById(chain.id).querySelector(".chain-steps");
    for (const chip of bar.children) chip.classList.remove("lit", "done");
  }
}

/**
 * Shows the payload inspector modal for one event.
 * @param {object} event - Normalized event.
 */
function inspect(event) {
  els.modalTitle.textContent = `#${event.sequence ?? "·"} ${event.kind}  ·  correlation ${event.correlation ?? "—"}`;
  els.modalJson.textContent = JSON.stringify({ [event.kind]: event.fields }, null, 2);
  els.modal.classList.remove("hidden");
}

els.modalClose.addEventListener("click", () => els.modal.classList.add("hidden"));
els.modal.addEventListener("click", (e) => {
  if (e.target === els.modal) els.modal.classList.add("hidden");
});

/**
 * Returns the accent-color lookup for all known missions.
 * @returns {Map<string, string>} missionId → color.
 */
function missionColors() {
  return new Map([...state.missions.values()].map((m) => [m.id, m.color]));
}

/**
 * Creates or returns one Mission record and its stage frame.
 * @param {?string} id - Mission identity.
 * @param {?string} [objective] - Known objective (wins over placeholder).
 * @returns {?object} Mission record or null without an id.
 */
function ensureMission(id, objective) {
  if (!id) return null;
  let mission = state.missions.get(id);
  if (!mission) {
    mission = {
      id,
      objective: objective ?? `Mission ${id}`,
      group: null,
      status: "Accepted",
      color: MISSION_PALETTE[state.missions.size % MISSION_PALETTE.length],
      plan: null,
      tasks: new Set(),
      taskStatus: new Map(),
    };
    state.missions.set(id, mission);
    stage.addMission(id, null, mission.color);
  } else if (objective && mission.objective.startsWith("Mission ")) {
    mission.objective = objective;
  }
  return mission;
}

/**
 * Registers a node as serving one Mission and refreshes cluster badges.
 * @param {?string} nodeId - Node identity.
 * @param {?string} missionId - Mission identity.
 */
function addNodeMission(nodeId, missionId) {
  if (!nodeId || !missionId) return;
  let set = state.nodeMissions.get(nodeId);
  if (!set) {
    set = new Set();
    state.nodeMissions.set(nodeId, set);
  }
  if (!set.has(missionId)) {
    set.add(missionId);
    stage.setNodes([...state.nodes.values()], state.nodeMissions, missionColors());
  }
}

/**
 * Builds a DAG-panel plan for one Mission (real plan or flat skeleton).
 * @param {?object} mission - Mission record.
 * @returns {?object} Plan-like document or null.
 */
function planForPanel(mission) {
  if (!mission) return null;
  if (mission.plan) return mission.plan;
  if (mission.tasks.size === 0) return null;
  return {
    mission: { id: mission.id, objective: mission.objective },
    tasks: [...mission.tasks].map((id) => ({ id, description: "", depends_on: [] })),
  };
}

/**
 * Focuses one Mission: mission list highlight, intelligence chips, DAG panel.
 * @param {?string} id - Mission id.
 */
function setFocus(id) {
  const mission = id ? state.missions.get(id) : null;
  state.focus = mission ? id : null;
  missionPanel.render(listMissions(), state.focus);
  const tasks = mission?.plan?.tasks
    ?? [...(mission?.tasks ?? [])].map((t) => ({ id: t }));
  stage.setFocus(state.focus, tasks, mission?.color ?? MISSION_PALETTE[0]);
  const plan = planForPanel(mission);
  if (plan) {
    dagPanel.setPlan(plan);
    for (const [taskId, status] of mission.taskStatus) dagPanel.setStatus(taskId, status);
  }
  if (mission) {
    stage.setMission(`MISSION ${mission.id} — ${mission.objective}`);
  }
}

/** @returns {Array<object>} Mission records in creation order. */
function listMissions() {
  return [...state.missions.values()];
}

/** Recomputes aggregate counters from per-Mission state. */
function refreshStats() {
  let done = 0;
  let total = 0;
  let active = 0;
  for (const mission of state.missions.values()) {
    for (const status of mission.taskStatus.values()) {
      total += 1;
      if (status === "Completed") done += 1;
      if (status === "Active") active += 1;
    }
  }
  statsPanel.update({ tasksDone: done, tasksTotal: total, active, nodes: state.nodes.size });
}

/**
 * Applies one normalized domain event to every console surface.
 * @param {object} event - Normalized event.
 */
function apply(event) {
  const entry = { ...entryFor(event.kind), kind: event.kind };
  const fields = event.fields;

  ticker.add(event);
  statsPanel.update({ events: statsPanel.counters.events + 1 });
  if (entry.chain) lightChain(entry.chain[0], entry.chain[1]);

  const missionId = missionIdOf(fields);

  // Task lifecycle propagation (mission-scoped).
  const taskEffect = entry.task ? entry.task(fields) : null;
  if (taskEffect?.taskRef) {
    const mission = ensureMission(taskEffect.taskRef.mission_id);
    const taskId = taskEffect.taskRef.task_id;
    mission.tasks.add(taskId);
    stage.ensureSlot(mission.id, taskId, mission.color);
    mission.taskStatus.set(taskId, taskEffect.status);
    if (state.focus === mission.id || !state.focus) dagPanel.setStatus(taskId, taskEffect.status);
    stage.setTaskStatus(mission.id, taskId, taskEffect.status);
    refreshStats();
  }

  // Node effects (also materialize unseen nodes for demo purposes).
  const nodeEffect = entry.node ? entry.node(fields) : null;
  if (nodeEffect?.id) {
    const patch = nodeEffect.state === "offline"
      ? { health: "Offline", liveness: "Unreachable", state: "offline" }
      : { state: nodeEffect.state };
    upsertNode(nodeEffect.id, patch);
  }

  // Node ↔ Mission association from selection/binding events.
  if (event.kind === "TaskSchedulingSelected") {
    for (const assignment of fields.assignments ?? []) {
      addNodeMission(assignment.node_id, missionId);
    }
  }
  if (event.kind === "MissionActorBound") addNodeMission(fields.node_id, missionId);
  if (event.kind === "RecoverySchedulingSelected" || event.kind === "RecoveryAssignmentCommitted") {
    addNodeMission(fields.replacement_node_id, missionId);
  }
  if (event.kind === "RecoveryRebound") addNodeMission(fields.to_node, missionId);

  // Calendar mutations.
  const calEffect = entry.cal ? entry.cal(fields) : null;
  if (calEffect) {
    calendarPanel.apply({
      key: taskKey(missionId ?? "?", calEffect.taskRef?.task_id ?? "?"),
      taskLabel: calEffect.taskRef?.task_id ?? "?",
      mission: missionId,
      phase: calEffect.phase,
      start: calEffect.start,
      end: calEffect.end,
    });
  }

  // Recovery ladder heat.
  const rung = entry.rung ? entry.rung(fields) : null;
  if (rung) recoveryPanel.fire(rung[0], rung[1]);

  // State & Memory plane activity.
  if (entry.layer === "smp") {
    statePanel.touch(3);
    if (event.kind.startsWith("Memory") || event.kind.startsWith("Map")) {
      statePanel.pushMemory(`${event.kind.replace(/^Memory|^Map/, "")} · ${(fields.manifest?.map_id ?? fields.artifact?.map_id ?? "revision")}`);
    }
  }

  // Mission lifecycle derivation.
  if (event.kind === "ExecutionGroupCreated") {
    const mission = ensureMission(fields.mission_id);
    if (!mission.group) {
      mission.group = fields.group_id;
      stage.addMission(mission.id, fields.group_id, mission.color);
    }
    mission.status = "Running";
    missionPanel.render(listMissions(), state.focus);
    if (state.mode === "live" && !mission.plan) discoverPlan(mission.id);
  }
  if (event.kind === "ExecutionGroupCompleted" && missionId) {
    const mission = ensureMission(missionId);
    mission.status = "Completed";
    missionPanel.render(listMissions(), state.focus);
  }
  if (event.kind === "ExecutionGroupFailed" && missionId) {
    const mission = ensureMission(missionId);
    mission.status = "Failed";
    missionPanel.render(listMissions(), state.focus);
  }

  stage.fire(entry, fields);
}

/**
 * Upserts one node into console state and refreshes the cluster grid.
 * @param {string} id - Node id.
 * @param {object} patch - Partial node fields.
 */
function upsertNode(id, patch) {
  const current = state.nodes.get(id) ?? {
    id, kindLabel: "node", health: "Unknown", liveness: "?", resourcesLabel: "—", state: "registered",
  };
  state.nodes.set(id, { ...current, ...patch, id });
  stage.setNodes([...state.nodes.values()], state.nodeMissions, missionColors());
  refreshStats();
}

/** Clears mission-scoped console state and panels (keeps live pollers). */
function resetMissionState() {
  for (const timer of state.timers) clearTimeout(timer);
  state.timers = [];
  if (state.missionTimer) clearInterval(state.missionTimer);
  state.missionTimer = null;
  state.missions = new Map();
  state.focus = null;
  state.nodes = new Map();
  state.nodeMissions = new Map();
  state.activeExecutions = 0;
  stage.reset();
  stage.setNodes([]);
  missionPanel.render([], null);
  dagPanel.reset();
  calendarPanel.reset();
  recoveryPanel.reset();
  statePanel.reset();
  ticker.reset();
  statsPanel.update({ events: 0, tasksDone: 0, tasksTotal: 0, active: 0, nodes: 0 });
  resetChains();
  els.cancel.disabled = true;
}

/** Clears timers and all console state for a fresh run. */
function resetAll() {
  resetMissionState();
  if (state.pollTimer) clearInterval(state.pollTimer);
  if (state.inventoryTimer) clearInterval(state.inventoryTimer);
  state.pollTimer = state.inventoryTimer = null;
  state.lastSequence = null;
}

/**
 * Loads and plays one demo scenario (single- or multi-Mission).
 * @param {object} demo - Demo descriptor.
 */
function runDemo(demo) {
  resetAll();
  state.mode = "demo";
  setConn("demo", "DEMO");
  const { events, totalMs } = buildDemoEvents(demo);
  const plans = demo.missions ?? [demo.plan];
  for (const plan of plans) {
    const mission = ensureMission(plan.mission.id, plan.mission.objective);
    mission.plan = plan;
    for (const task of plan.tasks) mission.tasks.add(task.id);
    stage.addMission(mission.id, `group-${plan.mission.id}`, mission.color);
    stage.addMissionTasks(mission.id, plan.tasks, mission.color);
  }
  for (const node of demo.nodes) state.nodes.set(node.id, node);
  stage.setNodes(demo.nodes, state.nodeMissions, missionColors());
  refreshStats();
  setFocus(plans[0].mission.id);
  lightChain("chain-decision", "Plan");
  els.cancel.disabled = false;
  for (const event of events) {
    state.timers.push(setTimeout(() => apply(decodeEvent(event)), event.delay));
  }
  state.timers.push(setTimeout(() => { els.cancel.disabled = true; }, totalMs + 800));
}

/** Live ingestion loop: polls the evidence log and applies new records. */
async function pollLive() {
  if (state.polling) return;
  state.polling = true;
  try {
    const raws = await api.events(state.lastSequence, 200);
    for (const raw of raws) {
      state.lastSequence = raw.sequence;
      apply(decodeEvent(raw));
    }
    setConn(state.lastSequence === null ? "demo" : "live",
      `LIVE · seq ${state.lastSequence ?? "—"}`);
  } catch (error) {
    setConn("error", `LIVE 掉线：${String(error.message ?? error).slice(0, 48)}`);
  } finally {
    state.polling = false;
  }
}

/**
 * Refreshes node cards from the live inventory projection.
 * @param {boolean} firstRun - Whether this is initial seeding.
 */
async function refreshInventory(firstRun = false) {
  try {
    const doc = await api.inventory();
    for (const node of doc.nodes ?? []) {
      upsertNode(node.node_id, {
        kindLabel: [...new Set((node.capabilities ?? []).map((c) => c.kind))].join("/") || "node",
        health: node.reported_health,
        liveness: node.liveness,
        resourcesLabel: (node.resources ?? []).map((r) => `${r.resource_id} ×${r.capacity}`).join(" · ") || "—",
        state: node.liveness === "Unreachable" ? "offline" : "registered",
      });
    }
    if (firstRun) setConn("live", "LIVE · 已连接");
  } catch (error) {
    setConn("error", `LIVE 掉线：${String(error.message ?? error).slice(0, 48)}`);
  }
}

/**
 * Polls authoritative Mission projections for every non-terminal Mission and
 * mirrors task lifecycles and execution relations onto the stage.
 */
async function trackActiveMissions() {
  for (const mission of listMissions()) {
    if (TERMINAL_STATUSES.has(mission.status)) continue;
    try {
      const doc = await api.mission(mission.id);
      if (mission.status !== doc.status) {
        mission.status = doc.status;
        missionPanel.render(listMissions(), state.focus);
      }
      for (const task of doc.tasks ?? []) {
        mission.tasks.add(task.task_id);
        stage.ensureSlot(mission.id, task.task_id, mission.color);
        const known = mission.taskStatus.get(task.task_id);
        if (known !== task.status) {
          mission.taskStatus.set(task.task_id, task.status);
          if (state.focus === mission.id || !state.focus) dagPanel.setStatus(task.task_id, task.status);
          stage.setTaskStatus(mission.id, task.task_id, task.status);
        }
      }
      for (const relation of doc.relations ?? []) {
        stage.setRelation(
          `${doc.group_id}/${relation.id}`,
          mission.id,
          relation.source.task_id,
          relation.target.task_id,
          relation.state,
        );
      }
      refreshStats();
    } catch { /* mission projection may not exist yet; events will teach us */ }
  }
}

/**
 * Rebuilds a plan skeleton for a live Mission from the federated State view.
 * The desired mission record carries objective and task ids but not edges, so
 * discovered DAGs render flat; console-submitted plans keep full edges.
 * @param {string} missionId - Discovered Mission identity.
 */
async function discoverPlan(missionId) {
  try {
    const doc = await api.stateRecords();
    const record = (doc.records ?? []).find((r) =>
      r.object?.object_type === "mission" && r.object?.object_id === missionId && r.semantic === "desired");
    const taskIds = record?.value?.task_ids ?? [];
    const mission = state.missions.get(missionId);
    if (!mission || !taskIds.length || mission.plan) return;
    mission.plan = {
      mission: { id: missionId, objective: record.value.objective ?? missionId },
      tasks: taskIds.map((id) => ({ id, description: "", depends_on: [] })),
    };
    for (const task of mission.plan.tasks) mission.tasks.add(task.id);
    stage.addMissionTasks(missionId, mission.plan.tasks, mission.color);
    if (mission.objective.startsWith("Mission ")) mission.objective = mission.plan.mission.objective;
    if (!state.focus || state.focus === missionId) setFocus(missionId);
    missionPanel.render(listMissions(), state.focus);
  } catch { /* state view unavailable; event stream still animates */ }
}

/**
 * Switches to live mode and connects to the proxied Controller.
 */
async function goLive() {
  resetAll();
  state.mode = "live";
  setConn("demo", "LIVE · 连接中…");
  try {
    await api.health();
  } catch (error) {
    setConn("error", `LIVE 不可达：${String(error.message ?? error).slice(0, 48)}`);
    return;
  }
  await refreshInventory(true);
  // Seed the calendar from the live Control projection.
  try {
    const doc = await api.schedulingReservations();
    for (const reservation of doc.reservations ?? []) {
      calendarPanel.apply({
        key: taskKey(reservation.mission_id ?? "?", reservation.task_id),
        taskLabel: reservation.task_id,
        mission: reservation.mission_id ?? null,
        phase: reservation.phase,
        start: reservation.starts_at_ms,
        end: reservation.ends_at_ms ?? undefined,
      });
    }
  } catch { /* calendar stays empty */ }
  await pollLive();
  state.pollTimer = setInterval(pollLive, 1600);
  state.inventoryTimer = setInterval(() => refreshInventory(false), 6000);
  state.missionTimer = setInterval(trackActiveMissions, 2500);
}

/**
 * Submits the selected scenario plan to the live Controller.
 */
async function submitLive() {
  const name = els.scenario.value;
  if (state.mode !== "live" || !LIVE_SCENARIOS.includes(name)) return;
  try {
    const plan = await api.scenarioPlan(name);
    const doc = await api.submit(plan);
    const mission = ensureMission(doc.mission_id, plan.mission.objective);
    mission.plan = plan;
    mission.group = doc.group_id;
    mission.status = "Accepted";
    for (const task of plan.tasks) mission.tasks.add(task.id);
    stage.addMission(mission.id, doc.group_id, mission.color);
    stage.addMissionTasks(mission.id, plan.tasks, mission.color);
    setFocus(mission.id);
    lightChain("chain-decision", "Plan");
    els.cancel.disabled = false;
    missionPanel.render(listMissions(), state.focus);
  } catch (error) {
    setConn("error", `提交失败：${String(error.message ?? error).slice(0, 60)}`);
  }
}

/**
 * Cancels the focused live Mission (live POST / demo local teardown).
 */
async function cancelActive() {
  if (state.mode === "live" && state.focus) {
    const mission = state.missions.get(state.focus);
    try {
      await api.cancel(state.focus);
      if (mission) {
        mission.status = "Cancelling";
        missionPanel.render(listMissions(), state.focus);
      }
    } catch (error) {
      setConn("error", `取消失败：${String(error.message ?? error).slice(0, 60)}`);
    }
    return;
  }
  for (const timer of state.timers) clearTimeout(timer);
  state.timers = [];
  for (const mission of listMissions()) {
    if (!TERMINAL_STATUSES.has(mission.status)) {
      mission.status = "Cancelled";
    }
  }
  missionPanel.render(listMissions(), state.focus);
  els.cancel.disabled = true;
}

/**
 * Updates the connection indicator.
 * @param {string} kind - live|demo|error.
 * @param {string} text - Status text.
 */
function setConn(kind, text) {
  els.conn.className = `conn conn-${kind}`;
  els.connText.textContent = text;
}

/** Populates the scenario selector for the current mode. */
function fillScenarios() {
  els.scenario.innerHTML = "";
  if (state.mode === "demo") {
    for (const demo of DEMOS) {
      const option = document.createElement("option");
      option.value = demo.id;
      option.textContent = demo.title;
      els.scenario.appendChild(option);
    }
  } else {
    for (const name of LIVE_SCENARIOS) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = `提交 ${name.replace(".json", "")}`;
      els.scenario.appendChild(option);
    }
  }
}

els.modeDemo.addEventListener("click", () => {
  els.modeDemo.classList.add("active");
  els.modeLive.classList.remove("active");
  fillScenarios();
  runDemo(DEMOS[0]);
});
els.modeLive.addEventListener("click", () => {
  els.modeLive.classList.add("active");
  els.modeDemo.classList.remove("active");
  fillScenarios();
  goLive();
});
els.run.addEventListener("click", () => {
  if (state.mode === "demo") {
    const demo = DEMOS.find((d) => d.id === els.scenario.value) ?? DEMOS[0];
    runDemo(demo);
  } else {
    submitLive();
  }
});
els.cancel.addEventListener("click", cancelActive);
els.reset.addEventListener("click", () => {
  if (state.mode === "demo") runDemo(DEMOS.find((d) => d.id === els.scenario.value) ?? DEMOS[0]);
  else goLive();
});

fillScenarios();

// Deep-links: `#live` connects to the real system; `#demo=<id>` plays a demo.
if (/^#?live$/.test(window.location.hash)) {
  els.modeLive.classList.add("active");
  els.modeDemo.classList.remove("active");
  fillScenarios();
  goLive();
} else {
  const requested = /^#?demo=(.+)$/.exec(window.location.hash)?.[1];
  const initial = DEMOS.find((d) => d.id === requested) ?? DEMOS[0];
  els.scenario.value = initial.id;
  runDemo(initial);
}
