/**
 * Layered SVG stage with a packet-flow animation engine.
 *
 * Geometry mirrors the implemented RoboGuide responsibility layers (mission
 * ingress, Mission Intelligence, the four Control Plane stations, Execution
 * Group, Runtime, Nodes, Physical World, and the horizontal State & Memory
 * Plane). The stage is multi-Mission: up to three Execution Group frames run
 * side by side, runtime slots and DAG chips are keyed by `mission/task`, and
 * the node layer renders the whole connected cluster with per-Mission badges.
 * All coordinates live in a fixed 1560×1030 viewBox that scales with the
 * container.
 */

import { CATEGORIES, missionIdOf } from "./model.js";

const NS = "http://www.w3.org/2000/svg";

/**
 * Creates one SVG element with attributes.
 * @param {string} tag - SVG tag name.
 * @param {object} attrs - Attribute map applied via `setAttribute`.
 * @param {string} [text] - Optional text content.
 * @returns {SVGElement} The created element.
 */
function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Control Plane station geometry: name → center x (y is fixed below). */
const STATIONS = {
  match: { cx: 182, title: "01 Capability Matching", q: "Who can?", out: "→ Candidate Set" },
  schedule: { cx: 450, title: "02 Embodied Scheduler", q: "Who should · Where · When?", out: "→ Assignment Proposal" },
  coordinate: { cx: 718, title: "03 Resource Coordination", q: "Contention → Reservation → Commit", out: "→ Committed Plan" },
  groupmgr: { cx: 986, title: "04 Group Manager", q: "Create · Bind · Adapt · Release", out: "→ Execution Group" },
};
const STATION_Y = 262;
const STATION_W = 240;
const STATION_H = 128;

/** Main column geometry (x span and per-layer y offsets). */
const COL = { x: 30, w: 1110, right: 1140 };
const ROWS = {
  mission: { y: 14, h: 64 },
  intelligence: { y: 96, h: 116 },
  control: { y: 232, h: 196 },
  group: { y: 448, h: 128 },
  runtime: { y: 596, h: 150 },
  nodes: { y: 766, h: 158 },
  world: { y: 944, h: 56 },
};
const CENTER_X = 585;
/** State & Memory Plane column geometry. */
const SMP = { x: 1166, y: 96, w: 364, h: 748 };

/** Execution Group frames: up to three side-by-side. */
const FRAMES = { x0: 56, w: 344, gap: 12, y: 462, h: 100, max: 3 };

/** Runtime execution slot grid: 6 columns × 2 rows, mission-scoped. */
const SLOTS = { x0: 56, w: 174, pitch: 182, rows: [642, 694], h: 48, max: 12 };

/** Cluster node card grid: 4 columns × 2 rows. */
const NODE_GRID = { x0: 40, w: 268, pitch: 276, rows: [810, 866], h: 50, max: 8 };

/** Mission Intelligence chip row (focused Mission tasks only). */
const CHIPS = { x0: 56, w: 150, pitch: 162, y: 152, h: 40, max: 8 };

/** Stateless category → packet color table. */
const CATEGORY_COLOR = Object.fromEntries(
  Object.entries(CATEGORIES).map(([id, meta]) => [id, meta.color]),
);

export class Stage {
  /** Builds all static geometry and starts the packet animation loop. */
  constructor(svg) {
    this.svg = svg;
    this.layers = {};
    this.stations = {};
    this.nodeCards = new Map();
    this.nodeList = [];
    this.slots = new Map();
    this.chips = new Map();
    this.groupFrames = new Map();
    this.relationPaths = new Map();
    this.packets = [];
    this.floats = [];
    this.lastFrame = 0;
    this.missionText = null;
    this.focusMission = null;
    this.#build();
    requestAnimationFrame((t) => this.#frame(t));
  }

  /** Creates defs, layer boxes, stations, SMP column, and static connectors. */
  #build() {
    const defs = el("defs");
    const marker = el("marker", {
      id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse",
    });
    marker.appendChild(el("path", { d: "M0,0 L10,5 L0,10 z", fill: "rgba(96,138,226,0.6)" }));
    defs.appendChild(marker);
    this.svg.appendChild(defs);

    this.gBack = el("g");
    this.gMid = el("g");
    this.gNodes = el("g");
    this.gFx = el("g");
    this.gPackets = el("g");
    this.svg.append(this.gBack, this.gMid, this.gNodes, this.gFx, this.gPackets);

    // Layer boxes (mission, intelligence, control, group, runtime, nodes, world).
    const layerMeta = [
      ["mission", "MISSION / APPLICATION", "外部只提交 Mission Request，不直接操作设备"],
      ["intelligence", "MISSION INTELLIGENCE", "Mission Understanding · Task Planning · Execution Requirements"],
      ["control", "CONTROL PLANE — 全局决策与协调", "Proposal ≠ Commit；Control reservation 是唯一 commitment authority"],
      ["group", "EMBODIED EXECUTION GROUPS", "Mission 级分布式执行上下文 · Create → Bind → Active → Adapt → Release"],
      ["runtime", "DISTRIBUTED EMBODIED RUNTIME", "live execution registry · ordered facts · relations · checkpoint"],
      ["nodes", "已接入集群 LOCAL EMBODIED SYSTEMS（roboguide-node + Local EAIOS）", "保留 Immediate How 与最终 Safety · 右上彩点 = 正在服务的 Mission"],
      ["world", "PHYSICAL WORLD", "走廊 · 物体 · 人 · 障碍 · 环境"],
    ];
    for (const [id, title, sub] of layerMeta) {
      const row = ROWS[id];
      const box = el("rect", {
        x: COL.x, y: row.y, width: COL.w, height: row.h, rx: 12, class: "lyr-box",
        style: `--lyr:${CATEGORY_COLOR.decision}`,
      });
      this.layers[id] = box;
      this.gBack.appendChild(box);
      this.gBack.appendChild(el("text", {
        x: COL.x + 16, y: row.y + 22, class: "lyr-title",
      }, title));
      this.gBack.appendChild(el("text", {
        x: COL.x + 16, y: row.y + 37, class: "lyr-sub",
      }, sub));
    }

    // Dashed spine connectors between layers.
    const spinePts = [
      [CENTER_X, ROWS.mission.y + ROWS.mission.h, ROWS.intelligence.y],
      [CENTER_X, ROWS.intelligence.y + ROWS.intelligence.h, ROWS.control.y],
      [CENTER_X, ROWS.control.y + ROWS.control.h, ROWS.group.y],
      [CENTER_X, ROWS.group.y + ROWS.group.h, ROWS.runtime.y],
      [CENTER_X, ROWS.runtime.y + ROWS.runtime.h, ROWS.nodes.y],
      [CENTER_X, ROWS.nodes.y + ROWS.nodes.h, ROWS.world.y],
    ];
    for (const [x, y1, y2] of spinePts) {
      this.gBack.appendChild(el("line", {
        x1: x, y1, x2: x, y2, class: "spine", "marker-end": "url(#arrow)",
      }));
    }

    // Control Plane stations.
    for (const [name, meta] of Object.entries(STATIONS)) {
      const g = el("g", { class: "station" });
      const rect = el("rect", {
        x: meta.cx - STATION_W / 2, y: STATION_Y, width: STATION_W, height: STATION_H,
        rx: 9, style: `--st:${CATEGORY_COLOR.decision}`,
      });
      g.appendChild(rect);
      g.appendChild(el("text", { x: meta.cx, y: STATION_Y + 30, "text-anchor": "middle" }, meta.title));
      g.appendChild(el("text", { x: meta.cx, y: STATION_Y + 52, "text-anchor": "middle", class: "q" }, meta.q));
      g.appendChild(el("text", { x: meta.cx, y: STATION_Y + STATION_H - 18, "text-anchor": "middle", class: "out" }, meta.out));
      this.gMid.appendChild(g);
      this.stations[name] = { g, rect };
    }
    const order = ["match", "schedule", "coordinate", "groupmgr"];
    for (let i = 0; i < order.length - 1; i++) {
      const x1 = STATIONS[order[i]].cx + STATION_W / 2 + 4;
      const x2 = STATIONS[order[i + 1]].cx - STATION_W / 2 - 6;
      const y = STATION_Y + STATION_H / 2;
      this.gBack.appendChild(el("line", {
        x1, y1: y, x2, y2: y, class: "spine", "marker-end": "url(#arrow)",
      }));
    }

    // State & Memory Plane column.
    this.gBack.appendChild(el("rect", {
      x: SMP.x, y: SMP.y, width: SMP.w, height: SMP.h, rx: 12, class: "smp-box",
    }));
    this.gBack.appendChild(el("text", {
      x: SMP.x + 20, y: SMP.y + 30, class: "smp-title",
    }, "STATE & MEMORY PLANE"));
    this.gBack.appendChild(el("text", {
      x: SMP.x + 20, y: SMP.y + 48, class: "lyr-sub",
    }, "横向基础设施 · Shared System View & Memory（scope / visibility / placement 分权）"),
    );
    this.smpItems = {};
    const items = [
      ["node-state", "Shared Node State — reported / observed + liveness"],
      ["capability", "Capability & Resource facts → eligibility"],
      ["allocation", "Allocation View — Committed / Bound / RecoveryPending"],
      ["memory", "Memory Catalog — execution·spatial·semantic·experience·artifact"],
      ["spatial", "Spatial Maps & Localization Evidence (CAS bytes)"],
      ["evidence", "Evidence Event Log — 本控制台的唯一数据源 /v1/events"],
    ];
    items.forEach(([id, label], i) => {
      const g = el("g", { class: "smp-item" });
      const y = SMP.y + 70 + i * 66;
      g.appendChild(el("rect", { x: SMP.x + 18, y, width: SMP.w - 36, height: 50 }));
      g.appendChild(el("text", { x: SMP.x + 32, y: y + 21 }, label.split(" — ")[0]));
      g.appendChild(el("text", { x: SMP.x + 32, y: y + 37 }, label.split(" — ")[1] ?? ""));
      this.gMid.appendChild(g);
      this.smpItems[id] = g;
    });
    this.gBack.appendChild(el("path", {
      d: `M ${COL.right} ${ROWS.runtime.y + 60} H ${SMP.x - 8}`, class: "smp-flow",
    }));
    this.gBack.appendChild(el("path", {
      d: `M ${COL.right} ${ROWS.nodes.y + 60} H ${SMP.x - 8}`, class: "smp-flow",
    }));
    this.gBack.appendChild(el("path", {
      d: `M ${SMP.x} ${ROWS.control.y + 70} H ${COL.right + 6}`, class: "smp-flow",
    }));

    // Physical world markers.
    this.gMid.appendChild(el("text", {
      x: CENTER_X, y: ROWS.world.y + 35, "text-anchor": "middle",
    }, "机器狗 · 货物 · 行人 · 障碍 · 区域 —— 执行改变世界，世界持续反馈 Observation"));

    // Mission placeholder text.
    this.missionText = el("text", {
      x: CENTER_X + 140, y: ROWS.mission.y + 50, "text-anchor": "middle",
      class: "float-label", opacity: 0.6,
    }, "等待 Mission 提交 —— 通过顶部「运行任务」开始一次旅程");
    this.gMid.appendChild(this.missionText);
  }

  /**
   * Re-renders the cluster grid from normalized node state.
   * @param {Array<object>} nodes - Normalized node records.
   * @param {Map<string, string[]>} badges - nodeId → serving Mission ids.
   * @param {Map<string, string>} missionColors - missionId → accent color.
   */
  setNodes(nodes, badges, missionColors) {
    for (const card of this.nodeCards.values()) card.g.remove();
    this.nodeCards.clear();
    this.nodeList = nodes.map((n) => n.id);
    const badgeList = badges ?? new Map();
    const colors = missionColors ?? new Map();
    nodes.slice(0, NODE_GRID.max).forEach((node, i) => {
      const col = i % 4;
      const row = Math.floor(i / 4);
      const x = NODE_GRID.x0 + col * NODE_GRID.pitch;
      const y = NODE_GRID.rows[row];
      const g = el("g", { class: `node-card ${node.state === "offline" ? "off" : ""}` });
      g.appendChild(el("title", {},
        `${node.id}\n${node.kindLabel ?? ""}\n${node.health ?? "?"} · ${node.liveness ?? "?"}\n${node.resourcesLabel ?? "—"}`));
      g.appendChild(el("rect", { x, y, width: NODE_GRID.w, height: NODE_GRID.h }));
      g.appendChild(el("text", { x: x + 12, y: y + 19, class: "nid" }, node.id));
      g.appendChild(el("text", { x: x + 12, y: y + 33, class: "nkind" }, (node.kindLabel ?? "node").slice(0, 34)));
      const health = el("text", { x: x + 12, y: y + 45, class: "nhealth" },
        `${node.health ?? "Unknown"} · ${node.liveness ?? "?"}`);
      health.style.fill = node.state === "offline" || node.health === "Offline" ? "#ff5d7a" : "#3dffb4";
      g.appendChild(health);
      const serving = [...(badgeList.get(node.id) ?? [])];
      serving.slice(0, 4).forEach((missionId, b) => {
        const color = colors.get(missionId) ?? CATEGORY_COLOR.group;
        g.appendChild(el("circle", {
          cx: x + NODE_GRID.w - 18 - b * 13, cy: y + 13, r: 4.5,
          fill: color, opacity: 0.9,
        }));
      });
      if (serving.length > 0) {
        g.appendChild(el("text", {
          x: x + NODE_GRID.w - 12, y: y + 44, "text-anchor": "end", class: "nkind",
        }, `${serving.length} mission`));
      }
      this.gNodes.appendChild(g);
      this.nodeCards.set(node.id, { g, health, state: node.state ?? "registered" });
    });
  }

  /** Marks one node card visually (registered|hot|offline). */
  markNode(id, state, color) {
    const card = this.nodeCards.get(id);
    if (!card) return;
    card.g.classList.remove("hot", "off");
    if (state === "offline") {
      card.g.classList.add("off");
      if (card.health) card.health.style.fill = "#ff5d7a";
    } else {
      if (card.health) card.health.style.fill = "#3dffb4";
    }
    if (state === "hot") {
      card.g.classList.add("hot");
      card.g.querySelector("rect")?.style.setProperty("--st", color ?? CATEGORY_COLOR.runtime);
    }
    if (state === "registered") {
      card.g.querySelector("rect")?.style.removeProperty("--st");
    }
  }

  /**
   * Creates one Execution Group frame for a Mission (up to three visible).
   * @param {string} missionId - Mission owning the group.
   * @param {string} groupId - Group identity for the label.
   * @param {string} color - Mission accent color.
   */
  addMission(missionId, groupId, color) {
    if (this.groupFrames.has(missionId) || this.groupFrames.size >= FRAMES.max) return;
    const index = this.groupFrames.size;
    const fx = FRAMES.x0 + index * (FRAMES.w + FRAMES.gap);
    const g = el("g", { class: "group-frame", "data-mission": missionId });
    g.appendChild(el("rect", { x: fx, y: FRAMES.y, width: FRAMES.w, height: FRAMES.h }));
    const label = el("text", { x: fx + 14, y: FRAMES.y + 20, style: `fill:${color}` },
      this.#shortId(missionId));
    const note = el("text", {
      x: fx + 14, y: FRAMES.y + FRAMES.h - 10, class: "lyr-sub", style: `fill:${color}`,
    }, this.#shortId(groupId ?? "", 40));
    g.append(label, note);
    this.gMid.appendChild(g);
    this.groupFrames.set(missionId, { g, label, note, x: fx, color, chips: new Map() });
    this.#applyFocusHighlight();
  }

  /**
   * Truncates a long identifier for stage labels.
   * @param {string} id - Raw identifier.
   * @param {number} [max] - Maximum length.
   * @returns {string} Shortened identifier.
   */
  #shortId(id, max = 26) {
    return id.length > max ? `${id.slice(0, max - 1)}…` : id;
  }

  /**
   * Updates one Mission's group frame state (active|blocked|released).
   * @param {?string} missionId - Mission identity.
   * @param {?string} effect - Frame effect class.
   */
  setGroupState(missionId, effect) {
    const frame = missionId && this.groupFrames.get(missionId);
    if (!frame) return;
    frame.g.classList.remove("active", "blocked", "released");
    if (effect) {
      frame.g.classList.add(effect);
      frame.label.textContent = `${this.#shortId(missionId)} · ${effect.toUpperCase()}`;
    }
  }

  /**
   * Ensures one mission-scoped runtime slot exists (idempotent).
   * @param {string} missionId - Mission identity.
   * @param {string} taskId - Task identity.
   * @param {string} color - Mission accent color.
   */
  ensureSlot(missionId, taskId, color) {
    const key = `${missionId}/${taskId}`;
    if (this.slots.has(key)) return this.slots.get(key);
    if (this.slots.size >= SLOTS.max) return null;
    const i = this.slots.size;
    const col = i % 6;
    const row = Math.floor(i / 6);
    const x = SLOTS.x0 + col * SLOTS.pitch;
    const y = SLOTS.rows[row];
    const g = el("g", { class: "exec-slot", "data-task": key });
    g.appendChild(el("rect", { x, y, width: SLOTS.w, height: SLOTS.h }));
    g.appendChild(el("rect", { x, y, width: 4, height: SLOTS.h, style: `fill:${color}`, opacity: 0.85 }));
    g.appendChild(el("text", { x: x + 12, y: y + 20 }, `▣ ${taskId}`.slice(0, 22)));
    const status = el("text", { x: x + 12, y: y + 37 }, "—");
    g.appendChild(status);
    this.gNodes.appendChild(g);
    const slot = { g, status, x, y, w: SLOTS.w, h: SLOTS.h, color };
    this.slots.set(key, slot);
    return slot;
  }

  /**
   * Ensures slots for a full Mission plan (idempotent).
   * @param {string} missionId - Mission identity.
   * @param {Array<{id: string}>} tasks - Plan tasks.
   * @param {string} color - Mission accent color.
   */
  addMissionTasks(missionId, tasks, color) {
    for (const task of tasks ?? []) this.ensureSlot(missionId, task.id, color);
  }

  /**
   * Re-renders the Mission Intelligence chip row for the focused Mission.
   * @param {?string} missionId - Focused Mission (null clears).
   * @param {Array<{id: string, description?: string}>} tasks - Focused tasks.
   * @param {string} color - Mission accent color.
   */
  setFocus(missionId, tasks, color) {
    this.focusMission = missionId;
    for (const chip of this.chips.values()) chip.remove();
    this.chips.clear();
    (tasks ?? []).slice(0, CHIPS.max).forEach((task, i) => {
      const x = CHIPS.x0 + i * CHIPS.pitch;
      const g = el("g", { class: "exec-slot" });
      g.appendChild(el("rect", { x, y: CHIPS.y, width: CHIPS.w, height: CHIPS.h }));
      g.appendChild(el("rect", { x, y: CHIPS.y, width: 3, height: CHIPS.h, style: `fill:${color}`, opacity: 0.85 }));
      g.appendChild(el("text", { x: x + 12, y: CHIPS.y + 17 }, `▲ ${task.id}`));
      g.appendChild(el("text", { x: x + 12, y: CHIPS.y + 31, class: "dag-desc" },
        (task.description ?? "").slice(0, 16)));
      this.gNodes.appendChild(g);
      this.chips.set(`${missionId}/${task.id}`, g);
    });
    this.#applyFocusHighlight();
  }

  /** Applies stroke emphasis to the focused Mission's group frame. */
  #applyFocusHighlight() {
    for (const [missionId, frame] of this.groupFrames) {
      frame.g.classList.toggle("focused", this.focusMission === missionId && this.groupFrames.size > 1);
    }
  }

  /** Updates one mission-scoped runtime slot's lifecycle text and class. */
  setTaskStatus(missionId, taskId, status) {
    const slot = this.slots.get(`${missionId}/${taskId}`);
    if (!slot) return;
    slot.g.classList.remove("ready", "active", "completed", "failed", "blocked");
    const cls = {
      Registered: "", Ready: "ready", Active: "active", Completed: "completed",
      Failed: "failed", Blocked: "blocked",
    }[status] ?? "";
    if (cls) slot.g.classList.add(cls);
    slot.status.textContent = status;
  }

  /** Shows the accepted mission objective inside the mission layer. */
  setMission(text) {
    this.missionText.textContent = text.length > 58 ? `${text.slice(0, 58)}…` : text;
    this.missionText.setAttribute("opacity", "1");
  }

  /** Clears all dynamic artifacts and in-flight packets. */
  reset() {
    for (const packet of this.packets) packet.el.remove();
    this.packets = [];
    for (const float of this.floats) float.el.remove();
    this.floats = [];
    for (const path of this.relationPaths.values()) path.el.remove();
    this.relationPaths.clear();
    for (const frame of this.groupFrames.values()) frame.g.remove();
    this.groupFrames.clear();
    for (const slot of this.slots.values()) slot.g.remove();
    this.slots.clear();
    for (const chip of this.chips.values()) chip.remove();
    this.chips.clear();
    this.focusMission = null;
    this.setMission("等待 Mission 提交 —— 通过顶部「运行任务」开始一次旅程");
  }

  /**
   * Draws or updates an execution relation arc between two mission-scoped slots.
   * @param {string} key - Relation storage key (group/relation scoped).
   * @param {string} missionId - Mission identity.
   * @param {string} sourceTask - Source task id.
   * @param {string} targetTask - Target task id.
   * @param {string} state - Runtime-reduced relation state.
   */
  setRelation(key, missionId, sourceTask, targetTask, state) {
    const from = this.slots.get(`${missionId}/${sourceTask}`);
    const to = this.slots.get(`${missionId}/${targetTask}`);
    if (!from || !to) return;
    const existing = this.relationPaths.get(key);
    const x1 = from.x + from.w / 2;
    const x2 = to.x + to.w / 2;
    const sameRow = Math.abs(from.y - to.y) < 4;
    const color = { Satisfied: "#3dffb4", Violated: "#ff5d7a", Pending: "#ffc857" }[state] ?? "#7d8db0";
    const d = sameRow
      ? `M ${x1} ${from.y - 8} C ${(x1 + x2) / 2} ${from.y - 38}, ${(x1 + x2) / 2} ${from.y - 38}, ${x2} ${to.y - 8}`
      : `M ${x1} ${from.y} C ${x1 + 40} ${(from.y + to.y) / 2}, ${x2 - 40} ${(from.y + to.y) / 2}, ${x2} ${to.y}`;
    if (existing) {
      existing.el.setAttribute("d", d);
      existing.el.setAttribute("stroke", color);
      existing.label.textContent = `${key.split("/").pop()}·${state}`;
      existing.state = state;
      return;
    }
    const path = el("path", { d, fill: "none", stroke: color, "stroke-width": 1.4, "stroke-dasharray": "3 4" });
    const label = el("text", {
      x: (x1 + x2) / 2, y: Math.min(from.y, to.y) - (sameRow ? 40 : 10), "text-anchor": "middle",
      "font-size": "8.5", "font-family": "var(--mono)",
    }, `${key.split("/").pop()}·${state}`);
    label.style.fill = color;
    this.gNodes.append(path, label);
    this.relationPaths.set(key, { el: path, label, state });
  }

  /**
   * Fires one catalog-mapped visual action onto the stage.
   * @param {object} entry - Catalog entry from `model.js`.
   * @param {object} fields - Event payload fields.
   * @returns {void}
   */
  fire(entry, fields) {
    const color = CATEGORY_COLOR[entry.cat] ?? CATEGORY_COLOR.system;
    const missionId = missionIdOf(fields);
    const route = this.#routeFor(entry.kind, entry, fields, color, missionId);
    if (!route) return;
    this.#spawn(route.pts, color, route);
  }

  /**
   * Computes the polyline route and arrival effects for one event kind.
   * @param {string} kind - Event variant name.
   * @param {object} entry - Catalog entry.
   * @param {object} fields - Event fields.
   * @param {string} color - Category color.
   * @param {?string} missionId - Owning Mission when mission-scoped.
   * @returns {?object} `{pts, pulses, float, arrive}` or null when silent.
   */
  #routeFor(kind, entry, fields, color, missionId) {
    const pulses = [];
    const push = (x, y, text) => pulses.push({ x, y, color, text });
    let pts = null;
    let floatOnArrive = true;
    const nodeId = fields.node_id ?? fields.replacement_node_id ?? fields.to_node ?? fields.previous_node_id;

    const stationBox = (name) => ({
      top: [STATIONS[name].cx, STATION_Y - 4],
      bottom: [STATIONS[name].cx, STATION_Y + STATION_H + 4],
    });
    const frameCenter = (mission) => {
      const frame = mission && this.groupFrames.get(mission);
      return frame ? frame.x + FRAMES.w / 2 : CENTER_X;
    };
    const frameTop = (mission) => [frameCenter(mission), FRAMES.y + 6];
    const frameBottom = (mission) => {
      const frame = mission && this.groupFrames.get(mission);
      return frame
        ? [frame.x + FRAMES.w / 2, FRAMES.y + FRAMES.h + 4]
        : [CENTER_X, ROWS.group.y + ROWS.group.h];
    };
    const nodeTop = (id) => {
      const i = this.nodeList.indexOf(id);
      const idx = i >= 0 ? Math.min(i, NODE_GRID.max - 1) : -1;
      const col = idx >= 0 ? idx % 4 : 1;
      const row = idx >= 0 ? Math.floor(idx / 4) : 0;
      return [NODE_GRID.x0 + col * NODE_GRID.pitch + NODE_GRID.w / 2, NODE_GRID.rows[row] + 6];
    };
    const slotEdge = (mission, task, edge) => {
      const slot = this.slots.get(`${mission}/${task}`);
      if (!slot) {
        return edge === "top" ? [CENTER_X, ROWS.runtime.y + 46] : [CENTER_X, ROWS.runtime.y + ROWS.runtime.h];
      }
      return [slot.x + slot.w / 2, edge === "top" ? slot.y - 4 : slot.y + slot.h + 4];
    };
    const toSmp = (fromX, fromY) =>
      [[fromX, fromY], [COL.right + 12, fromY], [COL.right + 12, SMP.y + 180], [SMP.x + 6, SMP.y + 180]];

    switch (kind) {
      case "CandidatesMatched": {
        const m = stationBox("match");
        pts = [[CENTER_X, ROWS.intelligence.y + ROWS.intelligence.h], [CENTER_X, ROWS.control.y - 12], [m.top[0], ROWS.control.y - 12], m.top];
        break;
      }
      case "TaskSchedulingSelected":
      case "TaskSchedulingDeferred":
      case "SchedulingReservationCreated": {
        const a = stationBox("match").bottom;
        const b = stationBox("schedule").top;
        pts = [a, [a[0], a[1] + 20], [b[0], b[1] + 20], b];
        break;
      }
      case "ProposalCreated": {
        const a = stationBox("schedule").bottom;
        const b = stationBox("coordinate").top;
        pts = [a, [a[0], a[1] + 20], [b[0], b[1] + 20], b];
        break;
      }
      case "PlanCommitted": {
        const a = stationBox("coordinate").bottom;
        const b = stationBox("groupmgr").top;
        pts = [a, [a[0], a[1] + 20], [b[0], b[1] + 20], b];
        break;
      }
      case "ExecutionGroupCreated":
      case "ExecutionGroupBound":
      case "MissionActorBound": {
        const g = stationBox("groupmgr").bottom;
        const target = frameTop(missionId);
        pts = [g, [g[0], ROWS.group.y - 14], [target[0], ROWS.group.y - 14], target];
        push(target[0], FRAMES.y + 10, "BIND");
        break;
      }
      case "TaskExecutionRegistered":
      case "TaskExecutionReady": {
        const from = frameBottom(missionId);
        const to = slotEdge(missionId, fields.task_ref?.task_id, "top");
        pts = [from, [to[0], ROWS.runtime.y - 6], to];
        break;
      }
      case "TaskExecutionActivated": {
        const from = slotEdge(missionId, fields.task_ref?.task_id, "bottom");
        const target = nodeId ? nodeTop(nodeId) : null;
        if (target) {
          pts = [from, [from[0], ROWS.nodes.y - 14], [target[0], ROWS.nodes.y - 14], target];
        } else {
          const fb = frameBottom(missionId);
          pts = [fb, [fb[0], ROWS.runtime.y - 6]];
        }
        break;
      }
      case "TaskExecutionCompleted": {
        const to = slotEdge(missionId, fields.task_ref?.task_id, "bottom");
        const from = nodeId ? nodeTop(nodeId) : [CENTER_X, ROWS.nodes.y + 40];
        pts = [from, [from[0], ROWS.nodes.y - 14], [to[0], ROWS.nodes.y - 14], to];
        break;
      }
      case "TaskExecutionFailed":
      case "RuntimeExecutionRecoveryRequired":
      case "ExecutionRelationReconciliationRequired": {
        const slotTop = slotEdge(missionId, fields.task_ref?.task_id, "top");
        pts = nodeId
          ? [nodeTop(nodeId), [nodeTop(nodeId)[0], ROWS.runtime.y - 10], slotTop]
          : [[CENTER_X, ROWS.runtime.y - 6], [CENTER_X, ROWS.runtime.y + 30]];
        break;
      }
      case "NodeRegistered":
      case "NodeHeartbeatAccepted":
      case "NodeObservation": {
        pts = null;
        const observed = nodeId ?? fields.TaskCompleted?.node_id ?? fields.TaskStarted?.node_id;
        if (observed) {
          const [x, y] = nodeTop(observed);
          push(x, y + 30, kind === "NodeRegistered" ? "ONLINE" : "OBS");
        }
        floatOnArrive = false;
        break;
      }
      case "NodeLeaseExpired": {
        const from = nodeId ? nodeTop(nodeId) : [CENTER_X, ROWS.nodes.y + 40];
        pts = toSmp(from[0], ROWS.nodes.y + 20);
        break;
      }
      case "StateRecordObserved":
      case "MemoryManifestPublished":
      case "MemoryArtifactStaged":
      case "MemoryArtifactImported":
      case "MemoryArtifactRejected":
      case "MapArtifactDeclared":
      case "MapArtifactPublished":
      case "MapArtifactStaged":
      case "MapArtifactImported":
      case "MapLocalizationVerified":
      case "MapLocalizationEvidenceRecorded":
      case "MapArtifactRejected": {
        const [x, y] = nodeId ? nodeTop(nodeId) : [CENTER_X, ROWS.runtime.y + 40];
        pts = toSmp(x, y);
        break;
      }
      case "ReconciliationRoleRecoveryRequired": {
        const gx = frameCenter(missionId);
        pts = [[gx, FRAMES.y + 6], [gx, ROWS.group.y - 14], [STATIONS.groupmgr.cx, ROWS.group.y - 14], stationBox("groupmgr").bottom];
        break;
      }
      case "RecoveryCandidatesMatched": {
        pts = [[SMP.x + 6, ROWS.control.y + 70], [COL.right - 4, ROWS.control.y + 70]];
        break;
      }
      case "RecoverySchedulingSelected":
      case "RecoverySchedulingNoSelection": {
        const a = stationBox("match").bottom;
        const b = stationBox("schedule").top;
        pts = [a, [a[0], a[1] + 20], [b[0], b[1] + 20], b];
        break;
      }
      case "RecoveryAssignmentProposed": {
        const a = stationBox("schedule").bottom;
        const b = stationBox("coordinate").top;
        pts = [a, [a[0], a[1] + 20], [b[0], b[1] + 20], b];
        break;
      }
      case "RecoveryAssignmentCommitted":
      case "RecoveryAssignmentAborted": {
        const a = stationBox("coordinate").bottom;
        const target = frameTop(missionId);
        pts = [a, [a[0], ROWS.group.y - 14], [target[0], ROWS.group.y - 14], target];
        push(target[0], FRAMES.y + 10, kind === "RecoveryAssignmentAborted" ? "ABORT" : "COMMIT");
        break;
      }
      case "RecoveryRebound": {
        const target = nodeTop(fields.to_node);
        const slotBottom = slotEdge(missionId, fields.task_ref?.task_id, "bottom");
        const gx = frameCenter(missionId);
        pts = [
          [STATIONS.coordinate.cx, STATION_Y + STATION_H + 4],
          [STATIONS.coordinate.cx, ROWS.group.y - 14], [gx, ROWS.group.y - 14], [gx, FRAMES.y + FRAMES.h + 8],
          [gx, ROWS.group.y + ROWS.group.h], [slotBottom[0], ROWS.runtime.y - 8],
          slotBottom,
          [slotBottom[0], ROWS.nodes.y - 14], [target[0], ROWS.nodes.y - 14], target,
        ];
        push(...target, "REBIND");
        break;
      }
      case "ExecutionRelationRegistered":
      case "ExecutionRelationStateChanged": {
        pts = null;
        floatOnArrive = false;
        break;
      }
      case "PeerChannelReadinessObserved": {
        const from = nodeId ? nodeTop(nodeId) : [CENTER_X, ROWS.nodes.y + 40];
        pts = [from, [from[0], ROWS.runtime.y - 10]];
        break;
      }
      default: {
        pts = null;
        floatOnArrive = false;
      }
    }

    // Arrival effects must be computed before the silence check: several real
    // events (Group Released, reservation activation, relation arcs, ...) have
    // no packet route but still mutate layer/station/node/group visuals.
    const layerPulse = entry.layer && this.layers[entry.layer]
      ? () => this.#flashLayer(entry.layer, color)
      : null;
    const stationPulse = entry.station && this.stations[entry.station]
      ? () => this.#pulseStation(entry.station, color)
      : null;
    const smpPulse = entry.layer === "smp"
      ? () => this.#pulseSmp(kind)
      : null;
    const nodeEffect = entry.node ? () => {
      const effect = entry.node(fields);
      if (effect?.id) this.markNode(effect.id, effect.state, color);
    } : null;
    const groupEffect = entry.group ? () => {
      const g = entry.group(fields);
      this.setGroupState(missionId, g.effect);
    } : null;
    const hasArriveEffect = Boolean(layerPulse ?? stationPulse ?? smpPulse ?? nodeEffect ?? groupEffect);
    if (!pts && pulses.length === 0 && !floatOnArrive && !hasArriveEffect) return null;

    return {
      pts,
      pulses,
      float: floatOnArrive ? entry.label(fields) : null,
      arrive: () => {
        layerPulse?.();
        stationPulse?.();
        smpPulse?.();
        nodeEffect?.();
        groupEffect?.();
      },
    };
  }

  /** Highlights the State & Memory item matching one event kind. */
  #pulseSmp(kind) {
    const map = {
      StateRecordObserved: ["node-state", "capability", "evidence"],
      NodeLeaseExpired: ["node-state"],
      MemoryManifestPublished: ["memory", "evidence"],
      MemoryArtifactStaged: ["memory"],
      MemoryArtifactImported: ["memory"],
      MemoryArtifactRejected: ["memory"],
      MapArtifactDeclared: ["spatial"],
      MapArtifactPublished: ["spatial"],
      MapArtifactStaged: ["spatial", "memory"],
      MapArtifactImported: ["spatial", "memory"],
      MapLocalizationVerified: ["spatial"],
      MapLocalizationEvidenceRecorded: ["spatial"],
      MapArtifactRejected: ["spatial"],
    }[kind] ?? ["evidence"];
    for (const id of map) {
      const item = this.smpItems[id];
      if (!item) continue;
      item.classList.add("hot");
      setTimeout(() => item.classList.remove("hot"), 1100);
    }
  }

  /** Briefly outlines a layer box in the category color. */
  #flashLayer(id, color) {
    const box = this.layers[id];
    if (!box) return;
    box.classList.add("active");
    box.style.setProperty("--lyr", color);
    setTimeout(() => box.classList.remove("active"), 900);
  }

  /** Briefly glows one Control Plane station. */
  #pulseStation(name, color) {
    const station = this.stations[name];
    if (!station) return;
    station.rect.classList.add("hot");
    station.rect.style.setProperty("--st", color);
    setTimeout(() => station.rect.classList.remove("hot"), 1100);
  }

  /** Spawns one animated packet along a polyline with arrival effects. */
  #spawn(pts, color, route) {
    if (route.arrive) {
      route.arrive();
    }
    for (const pulse of route.pulses ?? []) {
      const ring = el("circle", { cx: pulse.x, cy: pulse.y, r: 8, class: "pulse", stroke: pulse.color });
      this.gFx.appendChild(ring);
      ring.classList.add("run");
      setTimeout(() => ring.remove(), 950);
      if (pulse.text) this.#float(pulse.x, pulse.y - 12, pulse.text, pulse.color);
    }
    if (!pts || pts.length < 2) {
      if (route.float) {
        const [x, y] = pts?.[pts.length - 1] ?? [CENTER_X, ROWS.control.y];
        this.#float(x, y, route.float, color);
      }
      return;
    }
    const packet = el("g", { class: "packet" });
    packet.style.color = color;
    packet.appendChild(el("circle", { cx: 0, cy: 0, r: 4.2, class: "packet-body" }));
    packet.appendChild(el("circle", { cx: 0, cy: 0, r: 8, fill: "currentColor", opacity: 0.25 }));
    this.gPackets.appendChild(packet);
    this.packets.push({
      el: packet, pts, seg: 0, t: 0,
      speed: 0.42, color, float: route.float,
    });
  }

  /** Spawns a floating label that rises and fades near a stage point. */
  #float(x, y, text, color) {
    const node = el("text", { x, y, class: "float-label", "text-anchor": "middle" }, text);
    node.style.fill = color;
    this.gFx.appendChild(node);
    this.floats.push({ el: node, age: 0, x, y });
  }

  /** Advances packets and floating labels each animation frame. */
  #frame(now) {
    const dt = Math.min(48, now - (this.lastFrame || now));
    this.lastFrame = now;
    const done = [];
    for (const packet of this.packets) {
      let remain = packet.speed * dt;
      while (remain > 0 && packet.seg < packet.pts.length - 1) {
        const [x1, y1] = packet.pts[packet.seg];
        const [x2, y2] = packet.pts[packet.seg + 1];
        const len = Math.hypot(x2 - x1, y2 - y1) || 1;
        const left = (1 - packet.t) * len;
        if (remain < left) {
          packet.t += remain / len;
          remain = 0;
        } else {
          remain -= left;
          packet.seg += 1;
          packet.t = 0;
        }
      }
      if (packet.seg >= packet.pts.length - 1) {
        const [x, y] = packet.pts.at(-1);
        packet.el.remove();
        if (packet.float) this.#float(x, y - 8, packet.float, packet.color);
        done.push(packet);
        continue;
      }
      const [x1, y1] = packet.pts[packet.seg];
      const [x2, y2] = packet.pts[packet.seg + 1];
      const x = x1 + (x2 - x1) * packet.t;
      const y = y1 + (y2 - y1) * packet.t;
      packet.el.setAttribute("transform", `translate(${x} ${y})`);
    }
    this.packets = this.packets.filter((p) => !done.includes(p));
    const goneFloats = [];
    for (const float of this.floats) {
      float.age += dt;
      const k = float.age / 1600;
      if (k >= 1) {
        float.el.remove();
        goneFloats.push(float);
        continue;
      }
      float.el.setAttribute("opacity", String(1 - k));
      float.el.setAttribute("y", String(float.y - k * 26));
    }
    this.floats = this.floats.filter((f) => !goneFloats.includes(f));
    requestAnimationFrame((t) => this.#frame(t));
  }
}
