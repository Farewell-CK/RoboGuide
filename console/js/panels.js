/**
 * Side panels and event ticker for the RoboGuide console.
 *
 * All panels render from normalized console state only; they never call
 * RoboGuide APIs themselves.
 */

import { CATEGORIES, entryFor } from "./model.js";

const NS = "http://www.w3.org/2000/svg";

/**
 * Creates one SVG element with attributes.
 * @param {string} tag - SVG tag name.
 * @param {object} attrs - Attribute map.
 * @param {string} [text] - Text content.
 * @returns {SVGElement} Created element.
 */
function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * Escapes text for safe HTML interpolation.
 * @param {unknown} value - Raw value.
 * @returns {string} Escaped text.
 */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/**
 * Mission list with focus selection; one row per known Mission with its
 * lifecycle badge and accent color.
 */
export class MissionPanel {
  /**
   * @param {HTMLElement} root - #mission-card container.
   * @param {(missionId: string) => void} onFocus - Focus selection callback.
   */
  constructor(root, onFocus) {
    this.root = root;
    this.onFocus = onFocus;
  }

  /**
   * Renders the Mission list.
   * @param {Array<object>} missions - `{id, objective, group, status, color}`.
   * @param {?string} focusId - Focused Mission id.
   */
  render(missions, focusId) {
    if (!missions.length) {
      this.root.classList.add("empty");
      this.root.textContent = "等待任务提交";
      return;
    }
    this.root.classList.remove("empty");
    this.root.innerHTML = missions.map((m) => `
      <div class="mission-item ${m.id === focusId ? "focused" : ""}" data-id="${esc(m.id)}">
        <span class="m-dot" style="background:${m.color}"></span>
        <div class="m-body">
          <div class="m-id">${esc(m.id)}</div>
          <div class="m-obj" title="${esc(m.objective)}">${esc(m.objective)}</div>
        </div>
        <span class="lifecycle ${esc(m.status)}">${esc(m.status)}</span>
      </div>`).join("");
    for (const item of this.root.querySelectorAll(".mission-item")) {
      item.addEventListener("click", () => this.onFocus(item.dataset.id));
    }
  }
}

/** Task DAG view with longest-path layering and completion edge marking. */
export class DagPanel {
  /** @param {HTMLElement} root - #dag-view container. */
  constructor(root) { this.root = root; this.plan = null; this.status = new Map(); }

  /** @param {object} plan - MissionPlan document subset `{tasks}`. */
  setPlan(plan) { this.plan = plan; this.status = new Map(); this.#draw(); }

  /**
   * Updates one task status and redraws.
   * @param {string} taskId - Task id.
   * @param {string} status - Normalized lifecycle.
   */
  setStatus(taskId, status) {
    this.status.set(taskId, status);
    this.#draw();
  }

  /** Clears the view. */
  reset() { this.plan = null; this.status = new Map(); this.#draw(); }

  /** Redraws the DAG SVG. */
  #draw() {
    if (!this.plan) {
      this.root.classList.add("empty");
      this.root.textContent = "无任务图";
      return;
    }
    this.root.classList.remove("empty");
    const depth = new Map();
    for (const task of this.plan.tasks) {
      depth.set(task.id, Math.max(0, ...(task.depends_on ?? []).map((d) => (depth.get(d) ?? 0) + 1)));
    }
    const columns = new Map();
    for (const task of this.plan.tasks) {
      const d = depth.get(task.id);
      if (!columns.has(d)) columns.set(d, []);
      columns.get(d).push(task);
    }
    const maxDepth = Math.max(0, ...columns.keys());
    const maxColumnCount = Math.max(...[...columns.keys()].map((k) => columns.get(k).length));
    const W = Math.max(230, (maxDepth + 1) * 108);
    const H = Math.max(80, maxColumnCount * 62 + 16);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}` });
    const pos = new Map();
    for (const [d, tasks] of columns) {
      tasks.forEach((task, i) => {
        pos.set(task.id, [16 + d * 108, 14 + i * 62]);
      });
    }
    for (const task of this.plan.tasks) {
      for (const dep of task.depends_on ?? []) {
        const [x1, y1] = pos.get(dep);
        const [x2, y2] = pos.get(task.id);
        const edge = el("path", {
          d: `M ${x1 + 88} ${y1 + 18} C ${x1 + 102} ${y1 + 18}, ${x2 - 14} ${y2 + 18}, ${x2 - 2} ${y2 + 18}`,
          class: "dag-edge",
        });
        const depStatus = this.status.get(dep);
        if (depStatus === "Completed") edge.classList.add("flowed");
        svg.appendChild(edge);
      }
    }
    for (const task of this.plan.tasks) {
      const [x, y] = pos.get(task.id);
      const status = this.status.get(task.id) ?? "";
      const g = el("g", { class: `dag-node ${status.toLowerCase()}` });
      g.appendChild(el("rect", { x, y, width: 88, height: 36, rx: 7 }));
      g.appendChild(el("text", { x: x + 10, y: y + 15 }, task.id));
      g.appendChild(el("text", { x: x + 10, y: y + 28, class: "dag-desc" },
        status || (task.description ?? "").slice(0, 12)));
      svg.appendChild(g);
    }
    this.root.replaceChildren(svg);
  }
}

/**
 * Scheduling calendar (Control future intervals) as normalized Gantt bars.
 * Entries are keyed by `mission/task` so concurrent Missions never collide;
 * row labels add a Mission prefix only when task ids actually collide.
 */
export class CalendarPanel {
  /** @param {HTMLElement} root - #calendar-view container. */
  constructor(root) { this.root = root; this.intervals = new Map(); }

  /**
   * Applies one calendar mutation from an event.
   * @param {{key: string, taskLabel: string, mission: ?string, phase: string,
   *     start?: number, end?: number}} cal - Mission-scoped mutation.
   */
  apply(cal) {
    if (cal.phase === "Released") this.intervals.delete(cal.key);
    else this.intervals.set(cal.key, cal);
    this.#draw();
  }

  /** Clears the view. */
  reset() { this.intervals = new Map(); this.#draw(); }

  /** Redraws Gantt rows over the union window of all intervals. */
  #draw() {
    if (this.intervals.size === 0) {
      this.root.classList.add("empty");
      this.root.textContent = "无调度区间（仅未来 Ready Task 会出现）";
      return;
    }
    this.root.classList.remove("empty");
    const entries = [...this.intervals.entries()];
    const taskCounts = {};
    for (const [, cal] of entries) {
      taskCounts[cal.taskLabel] = (taskCounts[cal.taskLabel] ?? 0) + 1;
    }
    const short = (mission) => (mission ?? "").replace(/^mission-/, "").slice(0, 10);
    const starts = entries.map(([, c]) => c.start ?? Number.MAX_SAFE_INTEGER);
    const ends = entries.map(([, c]) => c.end ?? Number.MIN_SAFE_INTEGER);
    const lo = Math.min(...starts);
    const hi = Math.max(...ends);
    const span = Math.max(1, hi - lo);
    this.root.innerHTML = entries.map(([key, cal]) => {
      const left = cal.start === undefined ? 0 : ((cal.start - lo) / span) * 100;
      const width = cal.start === undefined || cal.end === undefined ? 100 : ((cal.end - cal.start) / span) * 100;
      const phase = cal.phase ?? "Scheduled";
      const label = taskCounts[cal.taskLabel] > 1 ? `${short(cal.mission)}·${cal.taskLabel}` : cal.taskLabel;
      return `<div class="cal-row">
        <div class="cal-task" title="${esc(key)}">${esc(label)}</div>
        <div class="cal-track"><div class="cal-bar ${esc(phase.toLowerCase())}"
          style="left:${left.toFixed(1)}%;width:${Math.max(6, width).toFixed(1)}%"
          title="${esc(key)} · ${esc(phase)}"></div></div>
      </div>`;
    }).join("");
  }
}

/** Recovery escalation ladder L0–L4. */
export class RecoveryPanel {
  /** @param {HTMLElement} root - #recovery-view container. */
  constructor(root) {
    this.root = root;
    this.levels = [
      ["L0", "Local Autonomy", "避障 · 短程重规划 · 安全停机"],
      ["L1", "Runtime", "重连 · 调用/通信恢复"],
      ["L2", "Execution Group", "成员替换 · re-bind · Group adaptation"],
      ["L3", "Scheduler / Coordination", "重新 Propose · Coordinate · Commit"],
      ["L4", "Mission Intelligence", "Task Graph 无法满足时重新规划"],
    ];
    this.hot = new Map();
    this.#draw();
  }

  /**
   * Marks one rung hot (or lets old heat decay).
   * @param {number} level - 0-based ladder level.
   * @param {string} hotness - `hot` or `warm`.
   */
  fire(level, hotness) {
    this.hot.set(level, { hotness, at: Date.now() });
    this.#draw();
    setTimeout(() => {
      const entry = this.hot.get(level);
      if (entry && Date.now() - entry.at >= 4200) {
        this.hot.delete(level);
        this.#draw();
      }
    }, 4300);
  }

  /** Clears active heat. */
  reset() { this.hot = new Map(); this.#draw(); }

  /** Redraws ladder rungs. */
  #draw() {
    this.root.innerHTML = `<div class="ladder">${this.levels.map(([lv, who, what], i) => {
      const heat = this.hot.get(i);
      const cls = heat ? (heat.hotness === "hot" ? "hot" : "warm") : "";
      return `<div class="rung ${cls}">
        <span class="lv">${lv}</span>
        <span><div class="who">${esc(who)}</div><div class="what">${esc(what)}</div></span>
      </div>`;
    }).join("")}</div>`;
  }
}

/** State & Memory Plane summary: providers with last-activity + memory rows. */
export class StatePanel {
  /** @param {HTMLElement} root - #state-view container. */
  constructor(root) {
    this.root = root;
    this.providers = [
      ["desired", "mission-orchestrator"],
      ["committed", "control-plane"],
      ["reported/observed", "shared-node-state"],
      ["derived", "runtime-orchestration"],
    ];
    this.memRows = [];
    this.recentAt = new Map();
    this.#draw();
  }

  /** Marks one provider recently active. */
  touch(providerIndex) {
    this.recentAt.set(providerIndex, Date.now());
    this.#draw();
    setTimeout(() => this.#draw(), 1200);
  }

  /**
   * Pushes one memory/catalog headline (kept to last 4).
   * @param {string} text - Headline.
   */
  pushMemory(text) {
    this.memRows.unshift(text);
    this.memRows = this.memRows.slice(0, 4);
    this.#draw();
  }

  /** Clears memory rows. */
  reset() { this.memRows = []; this.#draw(); }

  /** Redraws provider chips and memory rows. */
  #draw() {
    const now = Date.now();
    this.root.innerHTML = this.providers.map(([sem, owner], i) => {
      const recent = this.recentAt.get(i) && now - this.recentAt.get(i) < 1200;
      const color = { desired: "#9d7bff", committed: "#35e0ff", "reported/observed": "#3dffb4", derived: "#ffc857" }[sem];
      return `<div class="provider ${recent ? "recent" : ""}" style="color:${color}">
        <span class="dot" style="background:${color}"></span>
        <span style="color:var(--ink)">${esc(owner)}</span>
        <span style="margin-left:auto">${esc(sem)}</span>
      </div>`;
    }).join("") + this.memRows.map((row) => `<div class="mem-row">${esc(row)}</div>`).join("");
  }
}

/** Runtime statistics grid. */
export class StatsPanel {
  /** @param {HTMLElement} root - #stats-view container. */
  constructor(root) {
    this.root = root;
    this.counters = { events: 0, tasksDone: 0, tasksTotal: 0, active: 0 };
    this.#draw();
  }

  /**
   * Updates counters from console state.
   * @param {object} next - Partial counter overrides.
   */
  update(next) {
    Object.assign(this.counters, next);
    this.#draw();
  }

  /** Redraws stat tiles. */
  #draw() {
    const c = this.counters;
    this.root.innerHTML = `
      <div class="stat"><div class="v">${c.events}</div><div class="k">EVIDENCE EVENTS</div></div>
      <div class="stat"><div class="v">${c.tasksDone}/${c.tasksTotal}</div><div class="k">TASKS COMPLETED</div></div>
      <div class="stat"><div class="v">${c.active}</div><div class="k">ACTIVE EXECUTIONS</div></div>
      <div class="stat"><div class="v">${c.nodes ?? 0}</div><div class="k">NODES LIVE</div></div>`;
  }
}

/** Scrolling evidence event ticker with category filters and payload inspector. */
export class Ticker {
  /**
   * @param {HTMLElement} listRoot - #ticker-list container.
   * @param {HTMLElement} filterRoot - #ticker-filters container.
   * @param {(event: object) => void} onInspect - Payload modal callback.
   */
  constructor(listRoot, filterRoot, onInspect) {
    this.list = listRoot;
    this.filterRoot = filterRoot;
    this.onInspect = onInspect;
    this.events = [];
    this.active = new Set(Object.keys(CATEGORIES));
    this.max = 260;
    this.#buildFilters();
  }

  /** Renders category filter chips. */
  #buildFilters() {
    this.filterRoot.innerHTML = "";
    for (const [id, meta] of Object.entries(CATEGORIES)) {
      const chip = document.createElement("span");
      chip.className = "filter-chip on";
      chip.style.setProperty("--chip", meta.color);
      chip.textContent = meta.label;
      chip.addEventListener("click", () => {
        if (this.active.has(id) && this.active.size === 1) return;
        this.active.has(id) ? this.active.delete(id) : this.active.add(id);
        chip.classList.toggle("on", this.active.has(id));
        this.#render();
      });
      this.filterRoot.appendChild(chip);
    }
  }

  /**
   * Appends one normalized event (auto-scroll when pinned to bottom).
   * @param {object} event - Normalized event from `decodeEvent`.
   */
  add(event) {
    this.events.push(event);
    if (this.events.length > this.max) this.events.shift();
    this.#render(true);
  }

  /** Clears all rows. */
  reset() { this.events = []; this.#render(); }

  /** Redraws visible rows. */
  #render(animateLast = false) {
    const nearBottom = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 40;
    const visible = this.events.filter((e) => this.active.has(entryFor(e.kind).cat));
    this.list.innerHTML = visible.slice(-140).map((e, i, arr) => {
      const entry = entryFor(e.kind);
      const meta = CATEGORIES[entry.cat];
      const fresh = animateLast && i === arr.length - 1;
      let desc = "";
      try { desc = entry.desc(e.fields); } catch { desc = ""; }
      let label = e.kind;
      try { label = entry.label(e.fields); } catch { label = e.kind; }
      return `<div class="evt ${fresh ? "new" : ""}" data-seq="${e.sequence ?? ""}">
        <span class="seq">${e.sequence ?? "·"}</span>
        <span class="cat" style="--cat:${meta.color}">${meta.label}</span>
        <span class="name" style="color:${meta.color}">${esc(label)}</span>
        <span class="desc">${esc(desc)}</span>
      </div>`;
    }).join("");
    for (const row of this.list.querySelectorAll(".evt")) {
      row.addEventListener("click", () => {
        const seq = Number(row.dataset.seq);
        const found = this.events.find((e) => e.sequence === seq);
        if (found) this.onInspect(found);
      });
    }
    if (nearBottom) this.list.scrollTop = this.list.scrollHeight;
  }
}
