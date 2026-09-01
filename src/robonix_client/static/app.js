const $ = (id) => document.getElementById(id);
const maybe = (id) => document.getElementById(id);

// 多智能体注册表：每个 agent 包含 settings（从 /api/agents 拉取后回填）、
// 状态快照（来自 /api/system）、活跃 ws 连接等。
//
// activeAgentId —— 当前在 "agent-detail" 页打开的智能体；侧栏点击会更新它。
// activePage    —— "overview" | "agent-detail" | "maps"。
// agentsPage    —— overview 4 宫格当前页（每页 4 个）。
// wsByAgent     —— 每 agent 独立维护的 task / abort / voice / handsfree 连接，
//                 active agent 自动建连，切换时旧的保留以便切回。
const state = {
  agents: {},
  activeAgentId: null,
  defaultAgentId: "default",
  activePage: "overview",
  agentsPage: 0,
  wsByAgent: {},
  // 历史 settings 字段保留作为 default agent 的本地缓冲，让旧 collectSettings()
  // 调用（无 agentId）能继续工作而不至于 500。
  settings: {},
  sessionId: getSessionId(),
  sessionTitle: "",
  attachments: [],
  messages: [],
  timeline: [],
  plan: null,
  planRecords: [],
  taskState: null,
  batches: [],
  nodeStates: {},
  executorPlans: [],
  executorPlansReady: false,
  executorPlanIds: new Set(),
  executorMissingPolls: new Map(),
  // 注意：与上面的 agents 字典重名是历史包袱；这里特指聊天消息里正在
// 累积 agent 输出的那条消息 id。
  activeMessageAgentId: null,
  // 各 agent 的独立数据。键为 agentId；首次访问时按需初始化。
  // 切到另一 agent 时把 state.* 镜像到旧条目，再把新条目同步回 state。
  agentsById: {},
  history: loadConversations(),
  busy: false,
  taskRunning: false,
  activeStreams: 0,
  interactionSockets: new Set(),
  activeTurnId: "",
  activePilotSessionId: "",
  stopInFlight: false,
  voiceActive: false,
  activeVoiceSocket: null,
  activeVoiceMode: "voice",
  voiceFinishSupported: false,
  finishInFlight: false,
  // Whether the microphone is actually capturing right now. Distinct from
  // voiceActive, which stays true through ASR, Pilot, and TTS playback --
  // the finish control only makes sense while audio is still being recorded.
  voiceRecording: false,
  ttsPlaying: false,
  handsfree: { available: false, enabled: false, state: "unavailable", busy: false },
  handsfreeSocket: null,
  handsfreeReconnect: null,
  audio: {
    port: 60000,
    wsUrl: "",
    devices: [],
    inputCurrent: null,
    outputCurrent: null,
    vuSocket: null,
    logSocket: null,
    logLines: [],
    levelHistory: Array(28).fill(0),
    outputLevelTarget: 0,
    auraLevel: 0,
    auraFrame: 0,
    route: { micProviders: [], speakerProviders: [], micDevices: [], speakerDevices: [] },
  },
  overviewChat: {
    messages: [],
    sending: false,
    turnId: "",
  },
  modelName: "",
};

/// Name captured for a New session click that is still in flight.
///
/// Pressing the button blurs the name field first, so without this the typed
/// name would be committed as a rename of the session being left and the new
/// one would open unnamed -- two chats from one click. null means no click is
/// pending and the field behaves as a plain rename box.
let pendingNewSessionTitle = null;

const DEFAULT_ATLAS_PORT = 50051;
const AUDIO_LOG_MAX_LINES = 120;
const AUDIO_LOG_MAX_CHARS = 260;
const OVERVIEW_GRID_SIZE = 4;

// ── 多智能体桥接层 ────────────────────────────────────────────────
// state.agents 字典：{ [agentId]: { agentId, label, host, atlasPort, ..., settings, snapshot, status } }
function ensureAgentEntry(agentId) {
  if (!state.agents[agentId]) {
    state.agents[agentId] = {
      agentId,
      label: agentId,
      host: "",
      atlasPort: DEFAULT_ATLAS_PORT,
      userId: "",
      settings: {},
      snapshot: null,
      status: "unknown",
      sessionId: getSessionId(),
    };
  }
  return state.agents[agentId];
}

function listAgents() {
  return Object.values(state.agents).sort((a, b) => {
    if (a.agentId === state.defaultAgentId) return -1;
    if (b.agentId === state.defaultAgentId) return 1;
    return String(a.label || a.agentId).localeCompare(String(b.label || b.agentId));
  });
}

function activeAgent() {
  if (state.activeAgentId && state.agents[state.activeAgentId]) {
    return state.agents[state.activeAgentId];
  }
  const first = listAgents()[0];
  if (first) {
    state.activeAgentId = first.agentId;
    return first;
  }
  return ensureAgentEntry(state.defaultAgentId);
}

// ── per-agent 状态隔离 ─────────────────────────────────────────────
// state.sessionId/messages/timeline/plan/... 是当前 activeAgent 的视图。
// 切换 agent 时调用 snapshotAgentState() 把旧 agent 的视图写回
// state.agentsById[old]；切换后调用 restoreAgentState() 从
// state.agentsById[new] 取回（如不存在则用空模板）。
const AGENT_STATE_KEYS = [
  "sessionId", "sessionTitle", "attachments", "messages", "timeline",
  "plan", "planRecords", "batches", "nodeStates", "taskState",
  "activeMessageAgentId", "taskRunning", "busy", "activeTurnId",
  "activePilotSessionId", "stopInFlight",
];

function blankAgentState() {
  return {
    sessionId: getSessionId(),
    sessionTitle: "",
    attachments: [],
    messages: [],
    timeline: [],
    plan: null,
    planRecords: [],
    batches: [],
    nodeStates: {},
    taskState: null,
    activeMessageAgentId: null,
    taskRunning: false,
    busy: false,
    activeTurnId: "",
    activePilotSessionId: "",
    stopInFlight: false,
  };
}

function snapshotAgentState(agentId) {
  if (!agentId) return;
  const target = state.agentsById[agentId] || blankAgentState();
  for (const key of AGENT_STATE_KEYS) {
    if (key in state) target[key] = state[key];
  }
  state.agentsById[agentId] = target;
}

function restoreAgentState(agentId) {
  const source = state.agentsById[agentId] || blankAgentState();
  for (const key of AGENT_STATE_KEYS) {
    if (key in source) state[key] = source[key];
    else state[key] = blankAgentState()[key];
  }
  if (!state.sessionId) state.sessionId = getSessionId();
  if (!state.messages) state.messages = [];
  if (!state.timeline) state.timeline = [];
  if (!state.planRecords) state.planRecords = [];
  if (!state.batches) state.batches = [];
  if (!state.nodeStates) state.nodeStates = {};
}

// 返回指定 agent（或当前激活 agent）的 settings 字典。
// 旧调用方 collectSettings() 不传参时退回到当前激活 agent。
function getAgentSettings(agentId) {
  const id = agentId || state.activeAgentId || state.defaultAgentId;
  const entry = state.agents[id];
  if (entry && entry.settings && Object.keys(entry.settings).length) {
    return entry.settings;
  }
  return state.settings || {};
}

function buildAgentAtlas(settings) {
  const host = normalizeRobotHost(settings?.robotHost);
  const port = normalizeAtlasPort(settings?.atlasPort);
  return host ? `${host}:${port}` : (settings?.atlasEndpoint || "");
}

// /api/agents 返回的是公开视图（无 settings），拉详情时再 POST 拿完整 settings
async function refreshAgents() {
  let data = { agents: [] };
  try {
    const r = await fetch("/api/agents");
    data = await r.json();
  } catch (_) {
    return;
  }
  const incoming = new Set();
  for (const a of data.agents || []) {
    incoming.add(a.agentId);
    const entry = ensureAgentEntry(a.agentId);
    entry.agentId = a.agentId;
    entry.label = a.label || a.agentId;
    entry.host = a.host || "";
    entry.atlasPort = a.atlasPort || DEFAULT_ATLAS_PORT;
    entry.userId = a.userId || "";
    entry.lastSeen = a.lastSeen || 0;
  }
  // 清理已被删除的
  for (const id of Object.keys(state.agents)) {
    if (!incoming.has(id)) delete state.agents[id];
  }
  if (data.defaultAgentId) state.defaultAgentId = data.defaultAgentId;
  // 把每个 agent 的完整 settings 拉回来（公开视图不带 settings 字段）
  await Promise.all(listAgents().map((entry) => refreshAgentSettings(entry.agentId)));
  // 校正 activeAgent
  if (!state.agents[state.activeAgentId]) {
    const first = listAgents()[0];
    state.activeAgentId = first ? first.agentId : null;
  }
  renderNav();
  renderOverviewGrid();
  // 同步顶栏 agent 切换下拉
  syncAgentSelector();
}

async function refreshAgentSettings(agentId) {
  // 通过 POST /api/agents（空 body）返回 agent + settings 来回填。
  // 我们额外加一个临时端点不太经济，因此复用 upsert 行为：先 GET
  // /api/agents，再用同一个 id POST 一份来自 registry 视图的 settings；
  // 实际中通过后端 GET /api/agents 返回的 host 拼 settings 即可。
  const entry = state.agents[agentId];
  if (!entry) return;
  // 这里直接基于公开视图构造 settings（host/atlasPort/userId），
  // 用户编辑过的本地 override 仍存在 entry.settings 上时优先用。
  const fallback = {
    robotHost: entry.host,
    atlasPort: entry.atlasPort,
    userId: entry.userId,
    atlasEndpoint: buildAgentAtlas({ robotHost: entry.host, atlasPort: entry.atlasPort }),
  };
  entry.settings = { ...fallback, ...(entry.settings || {}) };
}

async function upsertAgent({ agentId, label, settings }) {
  const body = { agentId: agentId || "", label: label || "", settings: settings || {} };
  const r = await fetch("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!data.ok) throw new Error(data.error || "agent upsert failed");
  await refreshAgents();
  return data.agent;
}

async function renameAgentApi(agentId, label) {
  const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  const data = await r.json();
  if (!data.ok) throw new Error(data.error || "rename failed");
  return data.agent;
}

async function deleteAgentApi(agentId) {
  const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
  const data = await r.json();
  if (!data.ok) throw new Error(data.error || "delete failed");
  await refreshAgents();
}

async function refreshModel() {
  try {
    const r = await fetch("/api/model");
    const data = await r.json();
    state.modelName = data.model || "";
    setText("modelLabel", state.modelName ? `模型: ${state.modelName}` : "模型: 未设置");
  } catch (_) {
    setText("modelLabel", "模型: --");
  }
}

function getSessionId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// 轮询所有 agent 的 /api/system，更新 overview 状态卡和侧栏的 status 点。
// 不阻塞：每个 agent 单独一个 fetch，错误时仅标记为 offline。
async function refreshAllAgentsSnapshot() {
  const agents = listAgents();
  if (agents.length === 0) return;
  await Promise.all(agents.map(async (entry) => {
    if (!entry.host) {
      entry.snapshot = null;
      entry.status = "unknown";
      return;
    }
    const atlas = buildAgentAtlas({ robotHost: entry.host, atlasPort: entry.atlasPort });
    if (!atlas) {
      entry.snapshot = null;
      entry.status = "unknown";
      return;
    }
    try {
      const data = await fetch(`/api/system?atlas=${encodeURIComponent(atlas)}`).then((r) => r.json());
      entry.snapshot = data;
      entry.status = data.error ? "offline" : (data.summary?.state || "online");
    } catch (_) {
      entry.snapshot = { error: "snapshot fetch failed" };
      entry.status = "offline";
    }
    // Fetch live signals (camera/map URL, pose, battery) for the card.
    // Failures are silent — the card just keeps its placeholders.
    try {
      const live = await fetch(`/api/agents/${encodeURIComponent(entry.agentId)}/live`).then((r) => r.json());
      if (live && live.ok) {
        entry.snapshot = {
          ...(entry.snapshot || {}),
          cameraUrl: live.cameraUrl,
          mapUrl: live.mapUrl,
          mapName: live.mapName,
          pose: live.pose,
          battery: live.battery,
        };
      }
    } catch (_) { /* placeholder stays */ }
  }));
  renderNav();
  renderOverviewGrid();
  renderAgentDetail();
}


// ── 侧栏 + overview 渲染 ─────────────────────────────────────────
function renderNav() {
  const list = maybe("navAgents");
  if (!list) return;
  clear(list);
  const agents = listAgents();
  if (agents.length === 0) {
    const empty = document.createElement("div");
    empty.className = "nav-empty";
    empty.textContent = "暂无智能体，点击下方添加";
    list.appendChild(empty);
    return;
  }
  for (const entry of agents) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "nav-agent-item";
    btn.dataset.agentId = entry.agentId;
    if (entry.agentId === state.activeAgentId) btn.classList.add("active");
    const dot = document.createElement("span");
    dot.className = `nav-agent-dot status-${entry.status || "unknown"}`;
    const name = document.createElement("span");
    name.className = "nav-agent-name";
    name.textContent = entry.label || entry.agentId;
    const idPill = document.createElement("span");
    idPill.className = "nav-agent-id-pill";
    idPill.textContent = entry.agentId;
    idPill.title = `智能体 ID: ${entry.agentId}`;
    const sub = document.createElement("span");
    sub.className = "nav-agent-host";
    sub.textContent = entry.host ? `${entry.host}${entry.atlasPort ? `:${entry.atlasPort}` : ""}` : "(未配置主机)";
    btn.append(dot, name, idPill, sub);
    btn.addEventListener("click", () => selectAgent(entry.agentId));
    list.appendChild(btn);
  }
}

function syncAgentSelector() {
  const sel = maybe("activeAgentSelect");
  if (!sel) return;
  const agents = listAgents();
  const previous = sel.value;
  sel.replaceChildren();
  for (const entry of agents) {
    const opt = document.createElement("option");
    opt.value = entry.agentId;
    opt.textContent = entry.label || entry.agentId;
    sel.appendChild(opt);
  }
  if (state.activeAgentId && agents.some((a) => a.agentId === state.activeAgentId)) {
    sel.value = state.activeAgentId;
  } else if (previous && agents.some((a) => a.agentId === previous)) {
    sel.value = previous;
  }
  sel.onchange = () => selectAgent(sel.value);
}

function selectAgent(agentId) {
  if (!state.agents[agentId]) return;
  if (state.activeAgentId === agentId) {
    // 同一个 agent：仅刷新 UI
    renderNav();
    syncAgentSelector();
    syncAgentLabel();
    activatePage("agent-detail");
    renderAgentDetail();
    return;
  }
  // 把当前 activeAgent 的对话/计划/任务状态写回独立存储
  snapshotAgentState(state.activeAgentId);
  // 切到新 agent：恢复它的视图（如未存过则取空模板）
  const previous = state.activeAgentId;
  state.activeAgentId = agentId;
  restoreAgentState(agentId);
  renderNav();
  syncAgentSelector();
  syncAgentLabel();
  // 切到 agent-detail（每个智能体的操控界面）
  activatePage("agent-detail");
  renderAgentDetail();
  // 切换后让相关视图全部重渲染（聊天/计划/时间线）
  renderMessages();
  renderTimeline();
  renderPlan();
  renderSceneAssets();
  renderSessionChip();
  // 切到不同 agent 时把顶栏的"连接状态"等也刷一遍
  refreshSystem();
  refreshActivePlans();
  // 记录切换到时间线
  if (previous) {
    addTimeline("status", `切换到智能体 ${agentId}`);
  }
}

function syncAgentLabel() {
  const label = maybe("activeAgentLabel");
  if (!label) return;
  const agent = state.agents[state.activeAgentId];
  label.textContent = agent ? (agent.label || agent.agentId) : "未选择智能体";
}

// ── agent-detail 页面渲染 ───────────────────────────────────────────
// 智能体详情页是一个"该智能体的操控界面"的中转站：标题区显示当前智能体
// 身份和状态，tab 栏让用户跳转到该智能体的对话/状态监控/音频/地图/连接设置。
// 这里只渲染头部和 tab 高亮；具体内容由原 dashboard/vitals/audio/maps/settings
// 页面承担，通过 selectAgentTab() 切到对应 page 并保留 activeAgentId。
function renderAgentDetail() {
  const agent = state.agents[state.activeAgentId];
  const titleEl = maybe("agentDetailTitle");
  const idPill = maybe("agentDetailIdPill");
  const statusEl = maybe("agentDetailStatus");
  const subEl = maybe("agentDetailSub");
  const empty = maybe("agentDetailEmpty");
  const body = maybe("agentDetailBody");
  const renameBtn = maybe("agentDetailRename");
  const connectBtn = maybe("agentDetailConnect");
  const removeBtn = maybe("agentDetailRemove");
  if (!titleEl) return;
  if (!agent) {
    titleEl.textContent = "未选择智能体";
    if (idPill) idPill.textContent = "id: --";
    if (statusEl) {
      statusEl.textContent = "unknown";
      statusEl.className = "health-label unknown";
    }
    if (subEl) subEl.textContent = "请先在左侧选择或添加一个智能体";
    if (empty) empty.hidden = false;
    if (renameBtn) renameBtn.disabled = true;
    if (connectBtn) connectBtn.disabled = true;
    if (removeBtn) removeBtn.disabled = true;
    if (body) body.replaceChildren(empty);
    return;
  }
  titleEl.textContent = agent.label || agent.agentId;
  if (idPill) idPill.textContent = `id: ${agent.agentId}`;
  const summary = agent.snapshot?.summary;
  const stateLabel = summary?.state || agent.status || "unknown";
  if (statusEl) {
    statusEl.textContent = stateLabel;
    statusEl.className = `health-label ${statusKey(stateLabel)}`;
  }
  if (subEl) {
    const atlas = agent.host ? `${agent.host}:${agent.atlasPort || DEFAULT_ATLAS_PORT}` : "(未配置主机)";
    const lastSeen = agent.lastSeen ? ` · 最近活跃 ${new Date(agent.lastSeen * 1000).toLocaleTimeString()}` : "";
    subEl.textContent = `${atlas}${lastSeen}`;
  }
  if (empty) empty.hidden = true;
  if (renameBtn) renameBtn.disabled = agent.agentId === state.defaultAgentId;
  if (renameBtn) renameBtn.title = agent.agentId === state.defaultAgentId ? "default 智能体的名称始终等于主机 IP，无法重命名" : "修改该智能体的显示名称";
  if (removeBtn) removeBtn.disabled = agent.agentId === state.defaultAgentId;
  if (removeBtn) removeBtn.title = agent.agentId === state.defaultAgentId ? "default 智能体不能移除，请清空其设置" : "从多智能体系统中移除该智能体";
  if (connectBtn) connectBtn.disabled = false;
  // 默认 tab = 对话（如果还没选过）
  if (!state.activeAgentTab) state.activeAgentTab = "chat";
  highlightAgentTab(state.activeAgentTab);
  renderAgentDetailBody();
}

function selectAgentTab(name) {
  state.activeAgentTab = name;
  highlightAgentTab(name);
  // 把 tab 切换直接跳到对应的 page，方便用户继续操作
  const tabToPage = {
    chat: "dashboard",
    vitals: "vitals",
    audio: "audio",
    maps: "maps",
    settings: "settings",
  };
  const target = tabToPage[name] || "dashboard";
  activatePage(target);
}

function highlightAgentTab(name) {
  document.querySelectorAll("[data-agent-tab]").forEach((button) => {
    const active = button.dataset.agentTab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
}

function renderAgentDetailBody() {
  const body = maybe("agentDetailBody");
  const empty = maybe("agentDetailEmpty");
  if (!body) return;
  const agent = state.agents[state.activeAgentId];
  if (!agent) {
    if (empty) body.replaceChildren(empty);
    return;
  }
  body.replaceChildren();
  const summary = agent.snapshot?.summary;
  const contracts = agent.snapshot?.requiredContracts || [];
  const providers = agent.snapshot?.providers || [];
  const grid = document.createElement("div");
  grid.className = "agent-detail-summary";

  // 身份卡片
  const identity = document.createElement("section");
  identity.className = "agent-detail-card";
  identity.innerHTML = `
    <header>身份</header>
    <div class="agent-detail-card-row"><span>智能体 ID</span><strong>${escapeHtml(agent.agentId)}</strong></div>
    <div class="agent-detail-card-row"><span>显示名称</span><strong>${escapeHtml(agent.label || agent.agentId)}</strong></div>
    <div class="agent-detail-card-row"><span>主机 / IP</span><strong>${escapeHtml(agent.host || "(未配置)")}</strong></div>
    <div class="agent-detail-card-row"><span>Atlas 端口</span><strong>${agent.atlasPort || DEFAULT_ATLAS_PORT}</strong></div>
    <div class="agent-detail-card-row"><span>用户 ID</span><strong>${escapeHtml(agent.userId || "(默认)")}</strong></div>
  `;
  grid.appendChild(identity);

  // 连接快照
  const conn = document.createElement("section");
  conn.className = "agent-detail-card";
  const stateLabel = summary?.state || (agent.snapshot?.error ? "offline" : "unknown");
  const stateKey = statusKey(stateLabel);
  conn.innerHTML = `
    <header>连接快照</header>
    <div class="agent-detail-card-row"><span>运行状态</span><strong><span class="health-label ${stateKey}">${escapeHtml(stateLabel)}</span></strong></div>
    <div class="agent-detail-card-row"><span>活跃任务</span><strong>${summary?.active ?? 0}</strong></div>
    <div class="agent-detail-card-row"><span>错误数</span><strong>${summary?.errors ?? 0}</strong></div>
    <div class="agent-detail-card-row"><span>Provider 数量</span><strong>${providers.length}</strong></div>
    <div class="agent-detail-card-row"><span>必选契约</span><strong>${contracts.length}</strong></div>
    ${agent.snapshot?.error ? `<div class="agent-detail-card-row error"><span>错误</span><strong>${escapeHtml(String(agent.snapshot.error).slice(0, 200))}</strong></div>` : ""}
  `;
  grid.appendChild(conn);

  // 当前对话信息
  const chat = document.createElement("section");
  chat.className = "agent-detail-card";
  const lastUser = (state.messages || []).slice().reverse().find((m) => m.role === "user");
  const lastAgent = (state.messages || []).slice().reverse().find((m) => m.role === "agent");
  const lastTask = state.taskState || null;
  chat.innerHTML = `
    <header>当前会话</header>
    <div class="agent-detail-card-row"><span>会话名</span><strong>${escapeHtml(state.sessionTitle || "未命名会话")}</strong></div>
    <div class="agent-detail-card-row"><span>会话 ID</span><strong>${escapeHtml(String(state.sessionId || "").slice(0, 8))}</strong></div>
    <div class="agent-detail-card-row"><span>消息数</span><strong>${(state.messages || []).length}</strong></div>
    <div class="agent-detail-card-row"><span>最近用户消息</span><strong>${lastUser ? escapeHtml(truncate(lastUser.text || "(空)", 60)) : "(无)"}</strong></div>
    <div class="agent-detail-card-row"><span>最近智能体回复</span><strong>${lastAgent ? escapeHtml(truncate(lastAgent.text || "(空)", 60)) : "(无)"}</strong></div>
    <div class="agent-detail-card-row"><span>任务状态</span><strong>${escapeHtml(lastTask?.status || (state.taskRunning ? "运行中" : "空闲"))}</strong></div>
  `;
  grid.appendChild(chat);

  body.appendChild(grid);
}

function truncate(text, max) {
  const value = String(text || "");
  return value.length <= max ? value : `${value.slice(0, Math.max(0, max - 1))}…`;
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[ch]));
}

async function renameActiveAgent() {
  const agent = state.agents[state.activeAgentId];
  if (!agent || agent.agentId === state.defaultAgentId) return;
  const next = window.prompt("修改智能体显示名称", agent.label || agent.agentId);
  if (next === null) return;
  const cleaned = next.trim();
  if (!cleaned) return;
  try {
    const updated = await renameAgentApi(agent.agentId, cleaned);
    Object.assign(agent, { label: updated.label });
    renderNav();
    renderOverviewGrid();
    renderAgentDetail();
    syncAgentSelector();
    syncAgentLabel();
  } catch (err) {
    addStatusLine(`重命名失败: ${err.message || err}`);
  }
}

async function removeActiveAgent() {
  const agent = state.agents[state.activeAgentId];
  if (!agent || agent.agentId === state.defaultAgentId) return;
  if (!window.confirm(`确认移除智能体「${agent.label || agent.agentId}」？该操作不可撤销。`)) return;
  try {
    await deleteAgentApi(agent.agentId);
    addStatusLine(`已移除智能体 ${agent.agentId}`);
    // 删除后从 agentsById 清理
    delete state.agentsById[agent.agentId];
    if (state.wsByAgent[agent.agentId]) {
      try { state.wsByAgent[agent.agentId].close?.(); } catch (_) { /* noop */ }
      delete state.wsByAgent[agent.agentId];
    }
    // 退回 overview
    activatePage("overview");
  } catch (err) {
    addStatusLine(`移除失败: ${err.message || err}`);
  }
}

function connectActiveAgent() {
  refreshSystem();
  addStatusLine(`正在探测 ${state.activeAgentId || "(未选择)"} 的 Atlas ...`);
}

function renderOverviewGrid() {
  const grid = maybe("overviewGrid");
  if (!grid) return;
  clear(grid);
  const all = listAgents();
  if (all.length === 0) {
    const empty = document.createElement("div");
    empty.className = "overview-grid-empty";
    empty.textContent = "请先在左侧添加至少一个智能体";
    grid.appendChild(empty);
    const pager = maybe("overviewPager");
    if (pager) pager.hidden = true;
    return;
  }
  const totalPages = Math.max(1, Math.ceil(all.length / OVERVIEW_GRID_SIZE));
  if (state.agentsPage >= totalPages) state.agentsPage = totalPages - 1;
  if (state.agentsPage < 0) state.agentsPage = 0;
  const start = state.agentsPage * OVERVIEW_GRID_SIZE;
  const page = all.slice(start, start + OVERVIEW_GRID_SIZE);
  for (const entry of page) grid.appendChild(buildOverviewCard(entry));
  const pager = maybe("overviewPager");
  if (pager) {
    pager.hidden = totalPages <= 1;
    const info = maybe("overviewPageInfo");
    if (info) info.textContent = `第 ${state.agentsPage + 1} / ${totalPages} 页`;
  }
  const prev = maybe("overviewPrev");
  const next = maybe("overviewNext");
  if (prev) prev.disabled = state.agentsPage <= 0;
  if (next) next.disabled = state.agentsPage >= totalPages - 1;
}

function buildOverviewCard(entry) {
  const card = document.createElement("div");
  card.className = `overview-card status-${entry.status || "unknown"}`;
  card.addEventListener("click", (event) => {
    if (event.target.closest(".overview-card-close")) return;
    selectAgent(entry.agentId);
  });

  // ── 头部：状态点 + 名称 + ID + 关闭按钮 ─────────────────────────
  const head = document.createElement("header");
  const dot = document.createElement("span");
  dot.className = `nav-agent-dot status-${entry.status || "unknown"}`;
  const name = document.createElement("strong");
  name.textContent = entry.label || entry.agentId;
  head.append(dot, name);
  const idPill = document.createElement("span");
  idPill.className = "overview-card-id";
  idPill.textContent = entry.agentId;
  head.append(idPill);
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "overview-card-close";
  closeBtn.title = "关闭该智能体连接";
  closeBtn.setAttribute("aria-label", `关闭智能体 ${entry.agentId} 的连接`);
  closeBtn.textContent = "关闭";
  closeBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    closeAgentConnection(entry.agentId);
  });
  head.appendChild(closeBtn);

  // ── 上半部分：相机画面 ────────────────────────────────────────
  const camera = buildCameraSection(entry);

  // ── 下半部分：左地图 + 右状态面板 ─────────────────────────────
  const bottom = document.createElement("div");
  bottom.className = "overview-card-bottom";
  const mapSection = buildMapSection(entry);
  const statusPanel = buildStatusPanel(entry);
  bottom.append(mapSection, statusPanel);

  card.append(head, camera, bottom);
  return card;
}

// ── 相机画面 ─────────────────────────────────────────────────────
function buildCameraSection(entry) {
  const wrap = document.createElement("div");
  wrap.className = "overview-card-camera";
  const url = entry.snapshot?.cameraUrl;
  if (url) {
    const img = document.createElement("img");
    img.alt = `${entry.agentId} 相机画面`;
    img.loading = "lazy";
    // 加时间戳避免浏览器缓存旧帧
    img.src = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
    img.addEventListener("error", () => {
      wrap.replaceChildren();
      wrap.appendChild(buildCameraPlaceholder(entry, "无法加载相机画面"));
    });
    wrap.appendChild(img);
  } else {
    wrap.appendChild(buildCameraPlaceholder(entry, "未提供相机画面"));
  }
  const overlay = document.createElement("span");
  overlay.className = "overview-card-camera-overlay";
  overlay.textContent = `${entry.host || entry.agentId} · 相机`;
  wrap.appendChild(overlay);
  return wrap;
}

function buildCameraPlaceholder(entry, text) {
  const ph = document.createElement("div");
  ph.className = "overview-card-camera-placeholder";
  const icon = document.createElement("span");
  icon.className = "icon";
  icon.textContent = "📷";
  const label = document.createElement("span");
  label.textContent = text;
  ph.append(icon, label);
  return ph;
}

// ── 地图与位置 ──────────────────────────────────────────────────
function buildMapSection(entry) {
  const wrap = document.createElement("div");
  wrap.className = "overview-card-map";
  const mapUrl = entry.snapshot?.mapUrl;
  const pose = entry.snapshot?.pose;
  if (mapUrl) {
    const img = document.createElement("img");
    img.alt = `${entry.agentId} 当前地图`;
    img.loading = "lazy";
    img.src = mapUrl + (mapUrl.includes("?") ? "&" : "?") + "t=" + Date.now();
    img.addEventListener("error", () => {
      wrap.replaceChildren();
      wrap.appendChild(buildMapPlaceholder(entry));
    });
    wrap.appendChild(img);
  } else {
    wrap.appendChild(buildMapPlaceholder(entry));
  }
  // 位置标记
  if (pose && Number.isFinite(pose.x) && Number.isFinite(pose.y)) {
    const marker = document.createElement("div");
    marker.className = "overview-card-pose-marker";
    const theta = Number.isFinite(pose.theta) ? pose.theta : 0;
    marker.style.left = `${(pose.x * 100).toFixed(2)}%`;
    marker.style.top = `${((1 - pose.y) * 100).toFixed(2)}%`;
    marker.innerHTML =
      `<svg viewBox="0 0 14 14">` +
      `<polygon class="arrow" points="7,1 12,12 7,9 2,12" transform="rotate(${(theta * 180 / Math.PI).toFixed(1)} 7 7)"/>` +
      `</svg>`;
    if (pose.stale) marker.classList.add("is-stale");
    wrap.appendChild(marker);
  }
  const overlay = document.createElement("span");
  overlay.className = "overview-card-map-overlay";
  const mapName = entry.snapshot?.mapName || "未加载地图";
  overlay.textContent = mapName;
  wrap.appendChild(overlay);
  return wrap;
}

function buildMapPlaceholder(entry) {
  const ph = document.createElement("div");
  ph.className = "overview-card-map-placeholder";
  const icon = document.createElement("span");
  icon.className = "icon";
  icon.textContent = "🗺";
  const label = document.createElement("span");
  label.textContent = "暂无地图";
  ph.append(icon, label);
  return ph;
}

// ── 状态面板：状态 / 错误 / 电池 / 关闭 ──────────────────────────
function buildStatusPanel(entry) {
  const panel = document.createElement("div");
  panel.className = "overview-card-status";
  const summary = entry.snapshot?.summary || {};
  const battery = entry.snapshot?.battery;

  // 状态行
  const stateRow = document.createElement("div");
  stateRow.className = "overview-card-status-row";
  const stateLabel = document.createElement("span");
  stateLabel.className = "overview-card-status-label";
  stateLabel.textContent = "状态";
  const stateValue = document.createElement("span");
  stateValue.className = "overview-card-status-value";
  const stateText = entry.status === "online"
    ? (summary.state || "运行中")
    : entry.status === "offline"
      ? "离线"
      : "未连接";
  stateValue.textContent = stateText;
  if (entry.status === "online") stateValue.classList.add("good");
  else if (entry.status === "offline") stateValue.classList.add("bad");
  stateRow.append(stateLabel, stateValue);
  panel.appendChild(stateRow);

  // 任务行
  const taskRow = document.createElement("div");
  taskRow.className = "overview-card-status-row";
  const taskLabel = document.createElement("span");
  taskLabel.className = "overview-card-status-label";
  taskLabel.textContent = "任务";
  const taskValue = document.createElement("span");
  taskValue.className = "overview-card-status-value";
  if (summary.active) {
    taskValue.textContent = `运行 ${summary.active} · 错误 ${summary.errors || 0}`;
    taskValue.classList.add("good");
  } else {
    taskValue.textContent = "空闲";
  }
  taskRow.append(taskLabel, taskValue);
  panel.appendChild(taskRow);

  // 电池行
  const batteryRow = document.createElement("div");
  batteryRow.className = "overview-card-status-row";
  const batLabel = document.createElement("span");
  batLabel.className = "overview-card-status-label";
  batLabel.textContent = "电池";
  const batWrap = document.createElement("span");
  batWrap.className = "overview-card-battery";
  if (battery && Number.isFinite(battery.percent)) {
    const bar = document.createElement("span");
    bar.className = "overview-card-battery-bar";
    const fill = document.createElement("span");
    fill.className = "overview-card-battery-fill";
    const pct = Math.max(0, Math.min(100, battery.percent));
    fill.style.transform = `scaleX(${(pct / 100).toFixed(3)})`;
    if (pct < 15) fill.classList.add("critical");
    else if (pct < 35) fill.classList.add("low");
    bar.appendChild(fill);
    const value = document.createElement("span");
    value.className = "overview-card-status-value";
    value.textContent = `${pct.toFixed(0)}%`;
    if (pct < 15) value.classList.add("bad");
    else if (pct < 35) value.classList.add("warn");
    batWrap.append(bar, value);
  } else {
    const value = document.createElement("span");
    value.className = "overview-card-status-value";
    value.textContent = "--";
    const bar = document.createElement("span");
    bar.className = "overview-card-battery-bar";
    const fill = document.createElement("span");
    fill.className = "overview-card-battery-fill unknown";
    fill.style.transform = `scaleX(0)`;
    bar.appendChild(fill);
    batWrap.append(bar, value);
  }
  batteryRow.append(batLabel, batWrap);
  panel.appendChild(batteryRow);

  // 错误日志
  const errBox = document.createElement("div");
  errBox.className = "overview-card-status-errors";
  const errors = collectRecentErrors(entry);
  if (errors.length === 0) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = "无错误";
    errBox.appendChild(empty);
  } else {
    for (const line of errors) {
      const row = document.createElement("span");
      row.textContent = line;
      errBox.appendChild(row);
    }
  }
  panel.appendChild(errBox);
  return panel;
}

function collectRecentErrors(entry) {
  const out = [];
  const snap = entry.snapshot;
  if (!snap) return out;
  if (snap.error) out.push(`❌ ${snap.error}`);
  const providers = Array.isArray(snap.providers) ? snap.providers : [];
  for (const p of providers) {
    if (p && p.state === "ERROR") {
      out.push(`· ${p.id || p.kind || "?"} (${p.stateDetail || "ERROR"})`);
    }
  }
  return out.slice(0, 4);
}

// 断开指定智能体的连接：关闭其活动 WebSocket，
// 把状态置为 offline，并刷新 overview / sidebar 状态显示。
// 如果是当前激活的智能体，还会清理该智能体正在进行的交互连接。
function closeAgentConnection(agentId) {
  if (!agentId) return;
  // 关闭 per-agent ws 池
  const ws = state.wsByAgent?.[agentId];
  if (ws) {
    try { ws.close?.(); } catch (_) { /* noop */ }
    try { delete state.wsByAgent[agentId]; } catch (_) { /* noop */ }
  }
  // 若是当前激活的智能体，则把进行中的交互 socket 也一并关闭
  // （task / abort / voice 等是按 activeAgent 工作的）。
  if (state.activeAgentId === agentId) {
    for (const socket of [...state.interactionSockets]) {
      try { socket.close?.(); } catch (_) { /* noop */ }
    }
    state.interactionSockets.clear();
    state.activeStreams = 0;
    state.busy = false;
    state.taskRunning = false;
    setBusy(false);
  }
  const entry = state.agents[agentId];
  if (entry) {
    entry.status = "offline";
    entry.snapshot = { error: "connection closed by user", summary: { state: "offline" } };
  }
  addStatusLine(`已关闭智能体 ${agentId} 的连接`);
  renderNav();
  renderOverviewGrid();
  if (state.activeAgentId === agentId) {
    renderAgentDetail();
    syncAgentLabel();
  }
}

function overviewPrevPage() {
  if (state.agentsPage > 0) {
    state.agentsPage -= 1;
    renderOverviewGrid();
  }
}

function overviewNextPage() {
  const total = Math.max(1, Math.ceil(listAgents().length / OVERVIEW_GRID_SIZE));
  if (state.agentsPage < total - 1) {
    state.agentsPage += 1;
    renderOverviewGrid();
  }
}

// ── 添加智能体模态框 ─────────────────────────────────────────────
function openAddAgentModal() {
  const modal = maybe("addAgentModal");
  if (!modal) return;
  modal.hidden = false;
  const idEl = maybe("newAgentId");
  const labelEl = maybe("newAgentLabel");
  const hostEl = maybe("newAgentHost");
  const portEl = maybe("newAgentPort");
  const userEl = maybe("newAgentUser");
  if (idEl) idEl.value = "";
  if (labelEl) labelEl.value = "";
  if (hostEl) hostEl.value = "";
  if (portEl) portEl.value = String(DEFAULT_ATLAS_PORT);
  if (userEl) userEl.value = "";
  setText("addAgentError", "");
  idEl?.focus();
}

function closeAddAgentModal() {
  const modal = maybe("addAgentModal");
  if (modal) modal.hidden = true;
}

async function submitAddAgent(event) {
  event?.preventDefault?.();
  const idEl = maybe("newAgentId");
  const labelEl = maybe("newAgentLabel");
  const hostEl = maybe("newAgentHost");
  const portEl = maybe("newAgentPort");
  const userEl = maybe("newAgentUser");
  const agentId = (idEl?.value || "").trim();
  const label = (labelEl?.value || "").trim();
  const host = (hostEl?.value || "").trim();
  const port = normalizeAtlasPort(portEl?.value || DEFAULT_ATLAS_PORT);
  const userId = (userEl?.value || "").trim();
  if (!host) {
    setText("addAgentError", "请填写机器人 IP 或主机名");
    return;
  }
  try {
    const agent = await upsertAgent({
      agentId,
      label: label || host,
      settings: { robotHost: host, atlasPort: port, userId },
    });
    closeAddAgentModal();
    state.activeAgentId = agent.agentId;
    selectAgent(agent.agentId);
  } catch (err) {
    setText("addAgentError", `添加失败: ${err.message || err}`);
  }
}

function buildAgentSettingsFromEntry(entry) {
  return {
    robotHost: entry.host || "",
    atlasPort: entry.atlasPort || DEFAULT_ATLAS_PORT,
    userId: entry.userId || "",
    atlasEndpoint: buildAgentAtlas({ robotHost: entry.host, atlasPort: entry.atlasPort }),
  };
}

function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

function audioServerWsUrl(path) {
  if (!state.audio.wsUrl) return "";
  return `${state.audio.wsUrl.replace(/\/$/, "")}${path}`;
}

function saveSettings() {
  // 多 agent 模式下 settings 持久化由后端 /api/agents 完成，
  // 不再写 localStorage。此函数保留作为 no-op 兼容旧调用方。
  try {
    const snapshot = {};
    for (const [id, entry] of Object.entries(state.agents)) {
      snapshot[id] = entry.settings;
    }
    localStorage.setItem("robonix.agentSettings", JSON.stringify(snapshot));
  } catch (_) {
    // localStorage 满了也只是丢掉本地缓冲，后端还有持久化。
  }
}

async function persistSettings() {
  // 兼容旧调用方：把 active agent 的 settings 同步到后端 /api/agents。
  const agent = activeAgent();
  if (!agent) return { ok: false, error: "no active agent" };
  const result = await upsertAgent({
    agentId: agent.agentId,
    label: agent.label,
    settings: agent.settings,
  });
  return { ok: true, agent: result };
}

function normalizeRobotHost(raw) {
  return String(raw || "").trim();
}

function normalizeAtlasPort(raw) {
  const port = Number.parseInt(String(raw || "").trim(), 10);
  return Number.isFinite(port) && port > 0 ? port : DEFAULT_ATLAS_PORT;
}

function parseAtlasEndpoint(raw) {
  const value = String(raw || "").trim();
  if (!value) return { host: "", port: DEFAULT_ATLAS_PORT };
  const normalized = value.includes("://") ? value : `grpc://${value}`;
  try {
    const url = new URL(normalized);
    return {
      host: url.hostname || "",
      port: url.port ? Number.parseInt(url.port, 10) : DEFAULT_ATLAS_PORT,
    };
  } catch (_) {
    return { host: "", port: DEFAULT_ATLAS_PORT };
  }
}

function buildAtlasEndpoint(host, port) {
  const cleanHost = normalizeRobotHost(host);
  return cleanHost ? `${cleanHost}:${normalizeAtlasPort(port)}` : "";
}

function loadStoredSettings() {
  try {
    return JSON.parse(localStorage.getItem("robonix.settings") || "{}");
  } catch (_) {
    return {};
  }
}

function loadConversations() {
  try {
    const conversations = JSON.parse(localStorage.getItem("robonix.conversations") || "[]");
    if (Array.isArray(conversations)) return conversations;
  } catch (_) {
    // Fall through to one-time migration from the old prompt-only history.
  }
  try {
    const oldHistory = JSON.parse(localStorage.getItem("robonix.history") || "[]");
    if (!Array.isArray(oldHistory)) return [];
    return oldHistory.slice(0, 18).map((item) => ({
      id: getSessionId(),
      title: item.text || "Untitled chat",
      updatedAt: item.at || Date.now(),
      messages: item.text ? [{ id: getSessionId(), role: "user", text: item.text, meta: "" }] : [],
      timeline: [],
      plan: null,
      batches: [],
      nodeStates: {},
    }));
  } catch (_) {
    return [];
  }
}

/// Persist the conversation list, shedding load until it fits.
///
/// Attachments are base64 data URLs, so a couple of screenshots can carry a
/// 30-conversation history past localStorage's ~5MB quota. setItem then
/// throws, which used to propagate out of persistCurrentConversation and
/// skip the renderHistory() below it -- the sidebar entry vanished and the
/// write never landed, so a reload came back to an older snapshot.
///
/// Drop attachment payloads first (the transcript text is what the user came
/// back for), then the oldest conversations, and only give up once a single
/// conversation still will not fit.
function saveConversations() {
  const stripAttachments = (conversations) =>
    conversations.map((conversation) => ({
      ...conversation,
      messages: (conversation.messages || []).map(({ attachments, ...rest }) => ({
        ...rest,
        attachments: (attachments || []).map(({ dataUrl, ...meta }) => meta),
      })),
    }));

  const fits = (conversations) => {
    try {
      localStorage.setItem("robonix.conversations", JSON.stringify(conversations));
      return true;
    } catch (error) {
      if (error?.name !== "QuotaExceededError") throw error;
      return false;
    }
  };

  const capped = state.history.slice(0, 30);
  if (fits(capped)) return;
  // Attachment payloads are the bulk of the data and the least missed.
  let candidates = stripAttachments(capped);
  // history is newest-first, so trimming the tail sheds the oldest chats.
  while (candidates.length) {
    if (fits(candidates)) return;
    candidates = candidates.slice(0, candidates.length - 1);
  }
  // Never addStatusLine() here: it appends a message, which persists, which
  // lands back in this function.
  console.warn("robonix: browser storage is full; conversation history was not saved");
}

/// Record which conversation is on screen so a reload can return to it.
/// Without this every refresh landed in a brand-new empty session and the
/// work looked lost, even though it was still in the sidebar.
function rememberLastSession(sessionId) {
  if (sessionId) localStorage.setItem("robonix.lastSessionId", sessionId);
  else localStorage.removeItem("robonix.lastSessionId");
}

/// Reopen the conversation that was last on screen. Only restores one whose
/// transcript actually survived, so a stale id cannot strand the user in an
/// empty session that no longer exists.
function restoreLastSession() {
  const lastId = localStorage.getItem("robonix.lastSessionId") || "";
  if (!lastId) return false;
  const conversation = state.history.find((item) => item.id === lastId);
  if (!conversation) return false;
  state.sessionId = conversation.id;
  state.sessionTitle = conversation.title || "";
  state.messages = (conversation.messages || []).map((item) => ({ ...item }));
  state.timeline = (conversation.timeline || []).map((item) => ({ ...item }));
  state.plan = conversation.plan || null;
  state.planRecords = conversation.planRecords || [];
  state.batches = conversation.batches || [];
  state.nodeStates = conversation.nodeStates || {};
  return true;
}

async function init() {
  // 先拉取 agents 注册表。state.agents 在此之后才被填充。
  await refreshAgents();
  await refreshModel();
  const [defaults, persistedResult] = await Promise.all([
    fetch("/api/defaults").then((r) => r.json()).catch(() => ({})),
    fetch("/api/settings").then((r) => r.json()).catch(() => ({ settings: {} })),
  ]);
  const stored = (() => {
    try {
      return JSON.parse(localStorage.getItem("robonix.agentSettings") || "{}");
    } catch (_) {
      return {};
    }
  })();
  // 兼容旧的 robonix.settings（单 agent）到 default agent 上
  let legacyStored = {};
  try {
    legacyStored = JSON.parse(localStorage.getItem("robonix.settings") || "{}");
  } catch (_) {
    legacyStored = {};
  }
  const persisted = persistedResult.ok ? persistedResult.settings || {} : {};
  // CLI/environment values are launch defaults, not immutable policy. Stored
  // browser settings must win so changing robot host or audio routing survives
  // a refresh even when the client was initially launched with --robot-host.
  const atlas = parseAtlasEndpoint(defaults.atlasEndpoint || "");
  const baseSettings = {
    robotHost: defaults.robotHost || atlas.host || "",
    atlasPort: defaults.atlasPort || atlas.port || DEFAULT_ATLAS_PORT,
    liaisonEndpoint: "",
    userId: "",
    sessionTitle: "",
    recordSeconds: 30,
    language: "",
    micNodeId: "",
    micDeviceId: "",
    speakerNodeId: "",
    speakerDeviceId: "",
    ttsNodeId: "",
    enrollUserId: "",
    enrollUserName: "",
    ...defaults,
  };
  // 把持久化 settings 合并到 default agent；其它 agent 用各自 registry 视图。
  const defaultAgent = state.agents[state.defaultAgentId];
  if (defaultAgent) {
    defaultAgent.settings = {
      ...defaultAgent.settings,
      ...baseSettings,
      ...legacyStored,
      ...persisted,
      ...(stored[state.defaultAgentId] || {}),
    };
    defaultAgent.atlasPort = defaultAgent.settings.atlasPort || DEFAULT_ATLAS_PORT;
    defaultAgent.host = defaultAgent.settings.robotHost || defaultAgent.host;
  }
  // 其它 agent：用本地缓存覆盖
  for (const [id, cached] of Object.entries(stored)) {
    if (id === state.defaultAgentId) continue;
    const entry = state.agents[id];
    if (entry && cached && typeof cached === "object") {
      entry.settings = { ...entry.settings, ...cached };
    }
  }
  // state.settings 作为兼容旧调用方的回退
  state.settings = defaultAgent ? defaultAgent.settings : baseSettings;
  // An explicitly launched session id wins; otherwise come back to whatever
  // conversation was open before the reload instead of a fresh empty one.
  if (defaults.sessionId) state.sessionId = defaults.sessionId;
  else restoreLastSession();
  if (defaults.sessionTitle) state.sessionTitle = defaults.sessionTitle;
  rememberLastSession(state.sessionId);
  bindSettings();
  bindEvents();
  renderAudioBars();
  renderHistory();
  renderMessages();
  renderTimeline();
  renderPlan();
  renderSceneAssets();
  renderNav();
  renderOverviewGrid();
  activatePage("overview");
  refreshSystem();
  refreshActivePlans();
  refreshAudioRoute();
  refreshVoiceFinishSupport();
  // The speaking aura is visible on every page, so its physical output-level
  // stream must be connected at startup rather than only after opening Audio.
  checkAudioServer();
  setInterval(refreshSystem, 7000);
  setInterval(refreshActivePlans, 2000);
  setInterval(refreshHandsfree, 2500);
  setInterval(refreshVoiceFinishSupport, 7000);
  setInterval(refreshAgents, 15000);
  setInterval(refreshModel, 30000);
  // 多智能体总览：每 5 秒轮询一次所有 agent 的 /api/system，更新状态卡。
  setInterval(refreshAllAgentsSnapshot, 5000);
}

function bindSettings() {
  if (maybe("robotHost")) $("robotHost").value = state.settings.robotHost || "";
  if (maybe("robotHostSettings")) $("robotHostSettings").value = state.settings.robotHost || "";
  if (maybe("atlasPort")) $("atlasPort").value = state.settings.atlasPort || DEFAULT_ATLAS_PORT;
  if (maybe("atlasPortSettings")) $("atlasPortSettings").value = state.settings.atlasPort || DEFAULT_ATLAS_PORT;
  if (maybe("liaisonEndpoint")) $("liaisonEndpoint").value = state.settings.liaisonEndpoint || "";
  if (maybe("userId")) $("userId").value = state.settings.userId || "";
  if (maybe("settingsUserId")) $("settingsUserId").value = state.settings.userId || "";
  if (maybe("recordSeconds")) $("recordSeconds").value = state.settings.recordSeconds || 30;
  if (maybe("settingsRecordSeconds")) $("settingsRecordSeconds").value = state.settings.recordSeconds || 30;
  if (maybe("language")) $("language").value = state.settings.language || "";
  if (maybe("micNodeId")) $("micNodeId").value = state.settings.micNodeId || "";
  if (maybe("micDeviceId")) $("micDeviceId").value = state.settings.micDeviceId || "";
  if (maybe("speakerNodeId")) $("speakerNodeId").value = state.settings.speakerNodeId || "";
  if (maybe("speakerDeviceId")) $("speakerDeviceId").value = state.settings.speakerDeviceId || "";
  if (maybe("enrollUserId")) $("enrollUserId").value = state.settings.enrollUserId || "";
  if (maybe("enrollUserName")) $("enrollUserName").value = state.settings.enrollUserName || "";
  if (state.sessionTitle && maybe("promptTitle")) $("promptTitle").textContent = state.sessionTitle;
  renderSessionChip();

  [
    "robotHost",
    "robotHostSettings",
    "atlasPort",
    "atlasPortSettings",
    "liaisonEndpoint",
    "userId",
    "settingsUserId",
    "recordSeconds",
    "settingsRecordSeconds",
    "language",
    "micNodeId",
    "micDeviceId",
    "speakerNodeId",
    "speakerDeviceId",
    "enrollUserId",
    "enrollUserName",
  ].forEach((id) => maybe(id)?.addEventListener("change", syncConnectionSettings));
  ["settingsUserId", "settingsRecordSeconds"].forEach((id) => {
    maybe(id)?.addEventListener("change", () => syncConnectionSettings(true));
  });
  maybe("saveClientSettings")?.addEventListener("click", () => syncConnectionSettings(true, true));
}

async function syncConnectionSettings(fromSettings = false, persist = false) {
  const hostSource = (fromSettings || document.activeElement?.id === "robotHostSettings") && maybe("robotHostSettings") ? "robotHostSettings" : "robotHost";
  const portSource = (fromSettings || document.activeElement?.id === "atlasPortSettings") && maybe("atlasPortSettings") ? "atlasPortSettings" : "atlasPort";
  const host = maybe(hostSource) ? normalizeRobotHost($(hostSource).value) : "";
  const port = maybe(portSource) ? normalizeAtlasPort($(portSource).value) : DEFAULT_ATLAS_PORT;
  if (maybe("robotHost")) $("robotHost").value = host;
  if (maybe("robotHostSettings")) $("robotHostSettings").value = host;
  if (maybe("atlasPort")) $("atlasPort").value = port;
  if (maybe("atlasPortSettings")) $("atlasPortSettings").value = port;
  const userSource = (fromSettings || document.activeElement?.id === "settingsUserId") && maybe("settingsUserId") ? "settingsUserId" : "userId";
  const secondsSource = (fromSettings || document.activeElement?.id === "settingsRecordSeconds") && maybe("settingsRecordSeconds") ? "settingsRecordSeconds" : "recordSeconds";
  if (maybe("userId") && maybe(userSource)) $("userId").value = $(userSource).value.trim();
  if (maybe("settingsUserId") && maybe(userSource)) $("settingsUserId").value = $(userSource).value.trim();
  if (maybe("recordSeconds") && maybe(secondsSource)) $("recordSeconds").value = $(secondsSource).value;
  if (maybe("settingsRecordSeconds") && maybe(secondsSource)) $("settingsRecordSeconds").value = $(secondsSource).value;
  state.settings = collectSettings();
  saveSettings();
  window.dispatchEvent(new CustomEvent("robonix:settings"));
  if (!persist) {
    setText("settingsStatus", "本地已修改，请点击保存以持久化。");
    return;
  }
  setText("settingsStatus", "保存中...");
  try {
    const result = await persistSettings();
    setText("settingsStatus", `已保存到 ${result.path}。`);
  } catch (error) {
    setText("settingsStatus", `保存失败: ${error}`);
  }
}

function collectSettings(agentId) {
  // 多 agent 模式下从此函数读取，不再依赖 DOM。旧调用方不传参时
  // 回退到当前激活 agent。DOM 中的输入框（agent-detail Settings 子
  // 面板）由各自的事件回调写回对应 agent 的 settings，这里只读。
  const targetId = agentId || state.activeAgentId || state.defaultAgentId;
  const base = getAgentSettings(targetId) || {};
  const fallback = {
    robotHost: "",
    atlasPort: DEFAULT_ATLAS_PORT,
    atlasEndpoint: "",
    liaisonEndpoint: "",
    userId: "",
    recordSeconds: 30,
    language: "",
    micNodeId: "",
    micDeviceId: "",
    speakerNodeId: "",
    speakerDeviceId: "",
    ttsNodeId: "",
    enrollUserId: "",
    enrollUserName: "",
  };
  const merged = { ...fallback, ...base };
  // 若用户当前在 agent-detail 的 Settings 子面板编辑了 input，反映出来。
  // 这里只在 activeAgent 上做 DOM 同步，避免误改其它 agent。
  if (!agentId || agentId === state.activeAgentId) {
    const domHost = maybe("robotHostSettings")?.value?.trim() || maybe("robotHost")?.value?.trim();
    if (domHost) merged.robotHost = domHost;
    const domPort = maybe("atlasPortSettings")?.value?.trim() || maybe("atlasPort")?.value?.trim();
    if (domPort) merged.atlasPort = normalizeAtlasPort(domPort);
    const domLiaison = maybe("liaisonEndpoint")?.value?.trim();
    if (domLiaison) merged.liaisonEndpoint = domLiaison;
    const domUser = maybe("settingsUserId")?.value?.trim() || maybe("userId")?.value?.trim();
    if (domUser) merged.userId = domUser;
  }
  merged.atlasEndpoint = buildAgentAtlas(merged);
  return merged;
}

function interactionSettings(useActiveTurn = false) {
  const settings = collectSettings();
  if (useActiveTurn && state.activePilotSessionId) {
    settings.sessionId = state.activePilotSessionId;
  }
  return settings;
}

function bindEvents() {
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    sendTask();
  });
  $("taskInput").addEventListener("input", autoGrowInput);
  $("taskInput").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    $("composer").requestSubmit();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key !== "F2") return;
    event.preventDefault();
    if (state.voiceRecording) {
      if (state.voiceFinishSupported) finishVoiceCapture();
      else addStatusLine("该机器人不支持手动结束录音，需等待静音或达到录音时长上限。");
      return;
    }
    if (state.voiceActive) return;
    startVoice();
  });
  $("stopButton").addEventListener("click", stopCurrentTask);
  maybe("finishVoiceButton")?.addEventListener("click", finishVoiceCapture);
  maybe("voiceButton")?.addEventListener("click", startVoice);
  maybe("refreshSystem")?.addEventListener("click", refreshSystem);
  maybe("handsfreeToggle")?.addEventListener("click", toggleHandsfree);
  // The command bar's name field renames the open session as you type it;
  // the button beside it starts a new one under whatever name it holds.
  const sessionTitleInput = maybe("sessionTitleInput");
  if (sessionTitleInput) {
    sessionTitleInput.addEventListener("change", commitSessionTitle);
    sessionTitleInput.addEventListener("blur", commitSessionTitle);
    sessionTitleInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        sessionTitleInput.blur();
      }
    });
    // Clears a capture left behind by a click that never completed.
    sessionTitleInput.addEventListener("focus", () => {
      pendingNewSessionTitle = null;
    });
  }
  const newSessionAction = maybe("newSessionAction");
  if (newSessionAction) {
    // mousedown lands before the field's blur, which is the only moment the
    // typed name is still known to be meant for the session about to open.
    newSessionAction.addEventListener("mousedown", () => {
      pendingNewSessionTitle = maybe("sessionTitleInput")?.value.trim() || "";
    });
    newSessionAction.addEventListener("click", newSession);
  }
  $("renameSession").addEventListener("click", () => renameConversation(state.sessionId));
  $("clearHistory").addEventListener("click", clearHistory);
  maybe("connectNow")?.addEventListener("click", async () => {
    state.settings = collectSettings();
    await persistSettings().catch((error) => addTimeline("error", `保存设置失败: ${error}`));
    addTimeline("system", `正在连接 ${state.settings.robotHost}:${state.settings.atlasPort}`);
    refreshSystem();
  });
  maybe("startAudioServer")?.addEventListener("click", startAudioServer);
  maybe("checkAudioServer")?.addEventListener("click", checkAudioServer);
  maybe("refreshAudioDevices")?.addEventListener("click", loadAudioDevices);
  maybe("refreshAudioRoute")?.addEventListener("click", refreshAudioRoute);
  maybe("applyAudioRoute")?.addEventListener("click", applyAudioRoute);
  maybe("micNodeId")?.addEventListener("change", () => loadAudioRouteDevices("mic"));
  maybe("speakerNodeId")?.addEventListener("change", () => loadAudioRouteDevices("speaker"));
  maybe("enrollVoice")?.addEventListener("click", enrollVoice);
  maybe("testMicrophone")?.addEventListener("click", testMicrophone);
  maybe("testSpeaker")?.addEventListener("click", testSpeaker);
  maybe("mapsRefreshAll")?.addEventListener("click", refreshAllMaps);
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => activatePage(button.dataset.page));
  });
  document.querySelectorAll("[data-page-link]").forEach((button) => {
    button.addEventListener("click", () => activatePage(button.dataset.pageLink));
  });
  document.querySelectorAll("[data-page-action='voice-start']").forEach((button) => {
    button.addEventListener("click", startVoice);
  });
  maybe("openRtdlHistory")?.addEventListener("click", openRtdlHistory);
  maybe("closeRtdlHistory")?.addEventListener("click", closeRtdlHistory);
  maybe("openActiveRtdl")?.addEventListener("click", openActiveRtdl);
  maybe("closeActiveRtdl")?.addEventListener("click", closeActiveRtdl);
  maybe("activeRtdlModal")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeActiveRtdl();
  });
  maybe("rtdlHistoryModal")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeRtdlHistory();
  });
  // 多 agent 导航
  maybe("addAgentBtn")?.addEventListener("click", openAddAgentModal);
  maybe("addAgentCancel")?.addEventListener("click", closeAddAgentModal);
  maybe("addAgentCancel2")?.addEventListener("click", closeAddAgentModal);
  maybe("addAgentForm")?.addEventListener("submit", submitAddAgent);
  maybe("overviewPrev")?.addEventListener("click", overviewPrevPage);
  maybe("overviewNext")?.addEventListener("click", overviewNextPage);
  // overview 聊天广播
  maybe("overviewComposer")?.addEventListener("submit", (e) => {
    e.preventDefault();
    sendOverviewTask();
  });
  maybe("overviewSend")?.addEventListener("click", (e) => {
    e.preventDefault();
    sendOverviewTask();
  });
  // agent-detail 操控界面
  maybe("agentDetailRename")?.addEventListener("click", renameActiveAgent);
  maybe("agentDetailConnect")?.addEventListener("click", connectActiveAgent);
  maybe("agentDetailRemove")?.addEventListener("click", removeActiveAgent);
  document.querySelectorAll("[data-agent-tab]").forEach((button) => {
    button.addEventListener("click", () => selectAgentTab(button.dataset.agentTab));
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !maybe("activeRtdlModal")?.hidden) closeActiveRtdl();
    if (event.key === "Escape" && !maybe("rtdlHistoryModal")?.hidden) closeRtdlHistory();
    if (event.key === "Escape" && !maybe("addAgentModal")?.hidden) closeAddAgentModal();
  });
}

function openActiveRtdl() {
  const modal = maybe("activeRtdlModal");
  if (!modal) return;
  modal.hidden = false;
  refreshActivePlans();
  maybe("closeActiveRtdl")?.focus();
}

function closeActiveRtdl() {
  const modal = maybe("activeRtdlModal");
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  maybe("openActiveRtdl")?.focus();
}

function openRtdlHistory() {
  const modal = maybe("rtdlHistoryModal");
  if (!modal) return;
  modal.hidden = false;
  maybe("closeRtdlHistory")?.focus();
}

function closeRtdlHistory() {
  const modal = maybe("rtdlHistoryModal");
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  maybe("openRtdlHistory")?.focus();
}

async function configureReverseAudio(providerId) {
  if (!providerId) return { ok: false, skipped: true };
  const result = await fetch("/api/audio-reverse/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings(), providerId }),
  }).then((r) => r.json()).catch((error) => ({ ok: false, error: String(error) }));
  appendAudioLog(result.ok ? `reverse audio target ${result.target}` : `reverse audio error: ${result.error || "unknown"}`);
}

async function refreshHandsfree() {
  const button = maybe("handsfreeToggle");
  if (!button || state.handsfree.busy || !collectSettings().atlasEndpoint) return;
  const result = await fetch("/api/handsfree/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() }),
  }).then((r) => r.json()).catch((error) => ({ available: false, state: "unavailable", error: String(error) }));
  state.handsfree = { ...state.handsfree, ...result };
  renderHandsfree();
  syncHandsfreeEventStream();
}

async function toggleHandsfree() {
  if (state.voiceActive) {
    addStatusLine("请先结束当前的 F2 语音会话，再切换免提模式。");
    return;
  }
  if (state.handsfree.busy) return;
  state.handsfree.busy = true;
  renderHandsfree();
  const enabled = !state.handsfree.enabled;
  const result = await fetch("/api/handsfree/set", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings(), enabled }),
  }).then((r) => r.json()).catch((error) => ({ available: false, ok: false, state: "unavailable", error: String(error) }));
  state.handsfree = { ...state.handsfree, ...result, busy: false };
  renderHandsfree();
  syncHandsfreeEventStream();
  addTimeline(result.ok ? "voice" : "error", result.ok ? `机器人免提${enabled ? "已开启" : "已关闭"}` : `免提: ${result.error || result.detail || "不可用"}`);
}

function renderHandsfree() {
  const button = maybe("handsfreeToggle");
  const label = maybe("handsfreeState");
  if (!button || !label) return;
  const status = state.handsfree.state || "unavailable";
  const active = state.handsfree.enabled && ["starting", "listening", "triggered", "acknowledging", "in_voice"].includes(status);
  button.classList.toggle("offline", !active);
  button.classList.toggle("listening", status === "listening");
  button.classList.toggle("busy", state.handsfree.busy || ["triggered", "acknowledging", "in_voice"].includes(status));
  button.classList.toggle("error", status === "error" || status === "unavailable");
  label.textContent = state.handsfree.busy
    ? "Hands-free..."
    : status === "listening"
      ? "Listening"
      : status === "acknowledging"
        ? "Acknowledging"
      : status === "in_voice"
        ? "Hands-free active"
        : status === "suspended"
          ? "Recording"
        : state.handsfree.enabled
          ? `Hands-free ${status}`
          : "Hands-free off";
  button.title = state.handsfree.lastError || state.handsfree.error || (state.handsfree.keyword
    ? `Last wake phrase: ${state.handsfree.keyword}`
    : "Robot-local wake phrase configured by Speech");
  syncVoiceControls();
}

function handsfreeOwnsMicrophone() {
  return Boolean(state.handsfree.enabled && [
    "starting", "listening", "triggered", "acknowledging", "in_voice",
  ].includes(state.handsfree.state));
}

function syncVoiceControls() {
  const recordingBlocked = state.voiceActive && !state.ttsPlaying;
  const disabled = recordingBlocked;
  // While capture runs, the start control has nothing left to do and its
  // twin ("Stop recording") is what F2 now triggers. Leaving both on screen
  // showed two conflicting voice buttons at once, so hide this one outright
  // rather than only disabling it.
  const hideStart = state.voiceRecording && state.voiceFinishSupported;
  const title = state.ttsPlaying
      ? "Interrupt speech and start a new voice turn (F2)"
      : state.voiceActive
        ? "Voice recording is already active"
        : state.busy
          ? "Record a spoken instruction for the running task (F2)"
          : "Start voice recording (F2)";
  maybe("voiceButton")?.toggleAttribute("disabled", disabled);
  if (maybe("voiceButton")) $("voiceButton").title = title;
  document.querySelectorAll("[data-page-action='voice-start']").forEach((button) => {
    button.toggleAttribute("disabled", disabled);
    button.hidden = hideStart;
    button.title = title;
  });
  const micTest = maybe("testMicrophone");
  if (micTest) {
    const micBlocked = handsfreeOwnsMicrophone() || state.voiceActive;
    micTest.toggleAttribute("disabled", micBlocked);
    micTest.title = state.voiceActive
      ? "An F2 voice session owns this microphone. Stop it before testing the route."
      : handsfreeOwnsMicrophone()
        ? "Hands-free owns this microphone. Turn it off before running an exclusive microphone test."
        : "Capture one second through the selected Robonix microphone route.";
  }
  const handsfree = maybe("handsfreeToggle");
  if (handsfree) handsfree.toggleAttribute("disabled", state.voiceActive || state.handsfree.busy);
  const finishButton = maybe("finishVoiceButton");
  if (finishButton) {
    const show = state.voiceRecording && state.voiceFinishSupported;
    finishButton.hidden = !show;
    if (show && !state.finishInFlight) {
      finishButton.disabled = false;
      setButtonLabel(finishButton, "Stop recording");
    }
    finishButton.title = state.voiceFinishSupported
      ? "Stop recording and send what you have said so far (F2). Does not cancel the task."
      : "This robot does not advertise robonix/system/liaison/voice/finish, so recordings can only end on their own.";
  }
}

function stopHandsfreeEventStream() {
  if (state.handsfreeReconnect) {
    clearTimeout(state.handsfreeReconnect);
    state.handsfreeReconnect = null;
  }
  if (state.handsfreeSocket) {
    const socket = state.handsfreeSocket;
    state.handsfreeSocket = null;
    socket.close(1000, "hands-free disabled");
  }
}

function syncHandsfreeEventStream() {
  if (!state.handsfree.enabled || !collectSettings().atlasEndpoint) {
    stopHandsfreeEventStream();
    return;
  }
  const current = state.handsfreeSocket;
  if (current && [WebSocket.CONNECTING, WebSocket.OPEN].includes(current.readyState)) return;
  if (state.handsfreeReconnect) return;

  const socket = new WebSocket(wsUrl("/ws/handsfree-events"));
  state.handsfreeSocket = socket;
  socket.onopen = () => {
    socket.send(JSON.stringify({ settings: collectSettings() }));
    addStatusLine("正在监听机器人免提事件。");
  };
  socket.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "voice_event") handleVoiceEvent(payload.event);
    if (payload.type === "accepted") addTimeline("voice", "免提事件流已连接");
    if (payload.type === "error") addMessage("error", payload.error || "免提事件流异常");
  };
  socket.onclose = () => {
    if (state.handsfreeSocket === socket) state.handsfreeSocket = null;
    if (!state.handsfree.enabled) return;
    state.handsfreeReconnect = setTimeout(() => {
      state.handsfreeReconnect = null;
      syncHandsfreeEventStream();
    }, 1500);
  };
}

function autoGrowInput() {
  const input = $("taskInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

async function handleFiles(event) {
  const files = Array.from(event.target.files || []);
  for (const file of files) {
    if (!file.type.startsWith("image/")) continue;
    state.attachments.push(await readFile(file));
  }
  event.target.value = "";
  renderAttachments();
  renderSceneAssets();
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve({
        name: file.name,
        mediaType: file.type,
        size: file.size,
        dataUrl: reader.result,
      });
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function renderAttachments() {
  const strip = $("attachmentStrip");
  if (!strip) return;
  clear(strip);
  state.attachments.forEach((item, index) => {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "attachment-pill";
    pill.title = "Remove attachment";
    pill.textContent = item.name;
    pill.addEventListener("click", () => {
      state.attachments.splice(index, 1);
      renderAttachments();
    });
    strip.appendChild(pill);
  });
}

function activatePage(name) {
  state.activePage = name;
  document.querySelectorAll("[data-page]").forEach((button) => button.classList.toggle("active", button.dataset.page === name));
  document.querySelectorAll("[data-page-panel]").forEach((panel) => {
    const active = panel.dataset.pagePanel === name;
    panel.classList.toggle("active", active);
    // 旧路径完全靠 .active 来显隐；agent-detail 在 HTML 里默认 hidden，
    // 这里手动同步 hidden，避免同时显示两个 page。
    if (active) {
      panel.hidden = false;
    } else if (panel.classList.contains("page")) {
      panel.hidden = true;
    }
  });
  window.dispatchEvent(new CustomEvent("robonix:page", { detail: { name } }));
  if (name === "audio") {
    checkAudioServer();
  }
  if (name === "maps") {
    refreshAllMaps();
  }
  if (name === "overview") {
    renderOverviewGrid();
    // overview 聊天按需刷新
    refreshOverviewChat();
  }
  if (name === "agent-detail") {
    renderAgentDetail();
  }
  if (name === "dashboard" || name === "vitals" || name === "audio" || name === "settings") {
    // 切到子页时同步 activeAgent 视图（RTDL/audio 内部 fetch 会走 collectSettings）
    syncAgentLabel();
    refreshSystem();
  }
}

function refreshOverviewChat() {
  // 把 overview 聊天的"消息列表"重新渲染：每条用户指令 + 每个 agent 的
  // 实时回执按时间顺序排列。
  const root = maybe("overviewChatMessages");
  if (!root) return;
  clear(root);
  const messages = state.overviewChat?.messages || [];
  if (messages.length === 0) {
    const agents = listAgents();
    if (agents.length === 0) {
      const empty = document.createElement("div");
      empty.className = "message status";
      empty.textContent = "请先在左侧添加至少一个智能体";
      root.appendChild(empty);
    } else {
      const empty = document.createElement("div");
      empty.className = "message status";
      empty.textContent = "在下方输入框向大模型下达指令，指令会分发给全部已注册的智能体执行。";
      root.appendChild(empty);
    }
    return;
  }
  for (const msg of messages) {
    const node = renderOverviewChatMessage(msg);
    if (node) root.appendChild(node);
  }
  root.scrollTop = root.scrollHeight;
}

function renderOverviewChatMessage(msg) {
  if (!msg) return null;
  const el = document.createElement("div");
  el.className = `message ${msg.role || "status"}`;
  if (msg.role === "user") {
    const label = document.createElement("div");
    label.className = "overview-chat-label";
    label.textContent = "你 · 广播给全部智能体";
    el.appendChild(label);
    el.appendChild(document.createTextNode(msg.text || ""));
  } else if (msg.role === "agent") {
    const head = document.createElement("div");
    head.className = "overview-chat-agent-head";
    const id = document.createElement("span");
    id.className = "overview-chat-agent-id";
    id.textContent = msg.agentId || "(未知)";
    const state = document.createElement("span");
    state.className = `overview-chat-agent-state ${msg.state || "pending"}`;
    state.textContent = msg.state || "pending";
    head.append(id, state);
    el.appendChild(head);
    el.appendChild(document.createTextNode(msg.text || "(无回复)"));
  } else {
    el.textContent = msg.text || "";
  }
  return el;
}

function appendOverviewMessage(msg) {
  if (!state.overviewChat) state.overviewChat = { messages: [] };
  state.overviewChat.messages.push({ ...msg, at: Date.now() });
  // 限制消息条数，避免内存膨胀
  state.overviewChat.messages = state.overviewChat.messages.slice(-200);
  refreshOverviewChat();
}

// overview 聊天：向所有智能体广播同一条指令。返回时把每个 agent 的回执
// 标注 agentId 后渲染。
async function sendOverviewTask() {
  const input = maybe("overviewTaskInput");
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  const agents = listAgents();
  if (agents.length === 0) {
    addStatusLine("请先在左侧添加至少一个智能体");
    return;
  }
  if (state.overviewChat?.sending) {
    addStatusLine("上一次广播尚未完成，请稍候。");
    return;
  }
  if (state.overviewChat) state.overviewChat.sending = true;
  const sendBtn = maybe("overviewSend");
  if (sendBtn) sendBtn.disabled = true;
  appendOverviewMessage({ role: "user", text, agentId: "broadcast" });
  input.value = "";
  for (const entry of agents) {
    appendOverviewMessage({ role: "agent", agentId: entry.agentId, text: "正在派发...", state: "pending" });
  }
  await Promise.all(agents.map((entry) => dispatchOverviewTaskToAgent(entry, text)));
  if (state.overviewChat) state.overviewChat.sending = false;
  if (sendBtn) sendBtn.disabled = false;
}

// 对单个 agent 派发 overview 指令：新建一个临时 task WebSocket，把首条
// 智能体回复文本通过 overview 聊天 UI 渲染。完成后自动关闭。
async function dispatchOverviewTaskToAgent(entry, text) {
  const settings = buildAgentSettingsFromEntry(entry);
  if (!settings.atlasEndpoint) {
    updateOverviewAgentReply(entry.agentId, "未配置 Atlas 端点，无法派发", "failed");
    return;
  }
  const socket = new WebSocket(wsUrl("/ws/task"));
  let agentText = "";
  let finalText = "";
  let taskState = "pending";
  let receivedAny = false;
  await new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try { if (socket.readyState <= WebSocket.OPEN) socket.close(); } catch (_) { /* noop */ }
      resolve();
    };
    socket.onopen = () => {
      socket.send(JSON.stringify({
        text,
        settings: { ...settings, sessionId: getSessionId() },
        interactionMode: "task",
        steer: false,
        expectedTurnId: "",
        agentId: entry.agentId,
      }));
    };
    socket.onmessage = (event) => {
      receivedAny = true;
      const payload = JSON.parse(event.data);
      if (payload.type === "error") {
        updateOverviewAgentReply(entry.agentId, `错误: ${payload.error || "unknown"}`, "failed");
        finish();
        return;
      }
      const evt = payload.event || payload;
      if (payload.type === "pilot_event" && evt) {
        if (evt.kind === "text_chunk" && evt.textChunk) {
          agentText += evt.textChunk;
          updateOverviewAgentReply(entry.agentId, agentText, "running");
        } else if (evt.kind === "final_text" && evt.finalText) {
          finalText = mergeFinalText(finalText, evt.finalText);
          agentText = finalText;
          updateOverviewAgentReply(entry.agentId, finalText, "running");
        } else if (evt.kind === "task_state" && evt.taskState) {
          const status = String(evt.taskState.status || "").toLowerCase();
          if (["failed", "canceled", "cancelled", "aborted"].includes(status)) {
            taskState = "failed";
            updateOverviewAgentReply(entry.agentId, agentText || "(任务失败，无文本回复)", "failed");
            finish();
            return;
          }
          if (["done", "completed"].includes(status)) {
            taskState = "success";
          }
        }
      }
      if (payload.type === "status" && payload.message) {
        // 把 status 透传为追加
        if (!agentText) agentText = `[状态] ${payload.message}`;
      }
      if (payload.type === "done") {
        taskState = taskState === "failed" ? "failed" : (receivedAny ? "success" : "pending");
        updateOverviewAgentReply(entry.agentId, agentText || finalText || "(无回复)", taskState);
        finish();
      }
    };
    socket.onerror = () => {
      updateOverviewAgentReply(entry.agentId, "WebSocket 连接失败", "failed");
      finish();
    };
    socket.onclose = () => {
      taskState = taskState === "failed" ? "failed" : (receivedAny ? "success" : "pending");
      updateOverviewAgentReply(entry.agentId, agentText || finalText || "(无回复)", taskState);
      finish();
    };
  });
}

function updateOverviewAgentReply(agentId, text, state) {
  if (!state.overviewChat) state.overviewChat = { messages: [] };
  // 找到最后一条带这个 agentId 的 agent 消息
  for (let i = state.overviewChat.messages.length - 1; i >= 0; i -= 1) {
    const m = state.overviewChat.messages[i];
    if (m.role === "agent" && m.agentId === agentId) {
      m.text = text;
      if (state) m.state = state;
      refreshOverviewChat();
      return;
    }
  }
  appendOverviewMessage({ role: "agent", agentId, text, state });
}

/// Drop every pointer into the Pilot turn of the conversation being left.
///
/// interactionSettings() overrides the outgoing session id with
/// activePilotSessionId so a steer reaches the turn it belongs to. Any code
/// path that switches which conversation is on screen must clear these, or
/// the next message is delivered into the previous conversation's history
/// and the planner answers from turns the user never spoke there.
function forgetActiveTurn() {
  state.activeTurnId = "";
  state.activePilotSessionId = "";
  state.taskState = null;
  state.taskRunning = false;
}

function newSession() {
  if (state.busy) {
    pendingNewSessionTitle = null;
    addStatusLine("请先中止正在运行的任务，再开启新会话。");
    return;
  }
  // Captured at mousedown, before the field blurred. An untouched field still
  // shows the CURRENT session's name, and naming the new session after it
  // would clone the name on every click, so only text the user actually
  // changed counts as a name for the session about to open.
  const typedTitle = pendingNewSessionTitle ?? (maybe("sessionTitleInput")?.value.trim() || "");
  pendingNewSessionTitle = null;
  const requestedTitle = typedTitle && typedTitle !== state.sessionTitle ? typedTitle : "";
  persistCurrentConversation();
  state.sessionId = getSessionId();
  rememberLastSession(state.sessionId);
  forgetActiveTurn();
  state.sessionTitle = "";
  state.messages = [];
  state.timeline = [];
  state.plan = null;
  state.planRecords = [];
  state.batches = [];
  state.nodeStates = {};
  state.activeAgentId = null;
  state.sessionTitle = uniqueConversationTitle(requestedTitle || "未命名会话", state.sessionId);
  $("promptTitle").textContent = state.sessionTitle;
  renderSessionChip();
  // force: an empty transcript would otherwise fail the has-content check and
  // the new session would not appear in the sidebar until its first message.
  persistCurrentConversation("", true);
  // Pilot keys conversation history by session id, so a fresh id is what
  // actually drops the old turns from the next prompt. Say so -- against an
  // already-empty transcript the reset is otherwise invisible.
  addStatusLine("已开启新会话；规划器针对该会话的历史已清空。");
  addTimeline("status", `新建会话 ${state.sessionId.slice(0, 8)}`);
  renderMessages();
  renderTimeline();
  renderPlan();
  renderSceneAssets();
  renderHistory();
}

/// Keep the command bar's session chip showing the live conversation title.
/// It was static markup before, so a reset left the previous name on screen
/// and made the button look inert.
function renderSessionChip() {
  const field = maybe("sessionTitleInput");
  // Never clobber what the user is in the middle of typing.
  if (field && document.activeElement !== field) field.value = state.sessionTitle || "";
}

/// Apply the name field to the open session. Runs on change/blur rather than
/// on every keystroke so a half-typed name is not written to the sidebar.
/// An emptied field means "no explicit name", which leaves the conversation
/// on its message-derived title instead of naming it the empty string.
function commitSessionTitle() {
  const field = maybe("sessionTitleInput");
  if (!field) return;
  // A New session click is mid-flight and owns this text; renaming the
  // session being left with it is what produced two chats per click.
  if (pendingNewSessionTitle !== null) {
    renderSessionChip();
    return;
  }
  const typed = field.value.trim();
  if (!typed || typed === state.sessionTitle) {
    renderSessionChip();
    return;
  }
  const title = uniqueConversationTitle(typed, state.sessionId);
  state.sessionTitle = title;
  if (maybe("promptTitle")) $("promptTitle").textContent = title;
  persistCurrentConversation("", true);
  renderSessionChip();
}

async function sendTask() {
  const text = $("taskInput").value.trim();
  const attachments = state.attachments.slice();
  if (!text && attachments.length === 0) return;

  // A still-closing WebSocket or TTS tail is not an active Pilot turn. Only
  // mark input as steer while Pilot has task state that can actually accept it.
  const wasBusy = hasActiveTurn();
  state.activeVoiceMode = "voice";
  const display = text || attachments.map((item) => item.name).join(", ");
  addMessage("user", display, wasBusy ? "已加入运行中的任务" : (attachments.length ? `${attachments.length} 张图片` : ""), attachments);
  addStatusLine(wasBusy ? "已发送到运行中的任务，等待 Pilot 响应。" : "任务已提交，等待 Pilot 流式回复。");
  addTimeline("task", wasBusy ? `追加: ${display}` : `任务: ${display}`);
  persistCurrentConversation(display);
  $("taskInput").value = "";
  autoGrowInput();
  state.attachments = [];
  renderAttachments();
  renderSceneAssets();

  const socket = new WebSocket(wsUrl("/ws/task"));
  beginStream(socket);
  socket.onopen = () => {
    socket.send(JSON.stringify({
      text,
      attachments,
      settings: interactionSettings(wasBusy),
      steer: wasBusy,
      interactionMode: wasBusy ? "steer" : "task",
      expectedTurnId: wasBusy ? state.activeTurnId : "",
    }));
  };
  wireStream(socket, () => endStream(socket));
}

function stopCurrentTask() {
  if (!state.busy || state.stopInFlight) return;
  state.stopInFlight = true;
  const button = $("stopButton");
  button.disabled = true;
  setButtonLabel(button, "中止中");
  addStatusLine("已请求中止；正在取消所有运行中的任务和机器人动作。");
  addTimeline("cancel", `已请求中止${state.activeTurnId ? ` (${state.activeTurnId})` : ""}`);

  stopActiveVoiceSession();

  const socket = new WebSocket(wsUrl("/ws/abort"));
  socket.onopen = () => socket.send(JSON.stringify({
    settings: interactionSettings(true),
    expectedTurnId: state.activeTurnId,
  }));
  wireStream(socket, () => (socket.robonixDone ? completeStopState() : resetStopState()));
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "error") resetStopState();
  });
  socket.addEventListener("error", resetStopState);
}

function resetStopState() {
  state.stopInFlight = false;
  $("stopButton").disabled = false;
  setButtonLabel($("stopButton"), "中止全部任务");
}

function completeStopState() {
  state.taskRunning = false;
  state.activeTurnId = "";
  state.activePilotSessionId = "";
  state.taskState = state.taskState ? { ...state.taskState, status: "canceled" } : null;
  const sockets = [...state.interactionSockets];
  state.interactionSockets.clear();
  state.activeStreams = 0;
  sockets.forEach((socket) => {
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) socket.close();
  });
  setBusy(false);
  refreshActivePlans();
  renderPlan();
  persistCurrentConversation();
}

function stopActiveVoiceSession() {
  const socket = state.activeVoiceSocket;
  if (!state.voiceActive || !socket) return;
  const sendStop = () => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
  };
  if (socket.readyState === WebSocket.CONNECTING) socket.addEventListener("open", sendStop, { once: true });
  else sendStop();
}

function finishVoiceCapture() {
  // Distinct from stopActiveVoiceSession(): this submits what's been said
  // so far instead of discarding the turn, for when background noise keeps
  // the ASR backend's own silence detection from ever firing.
  const socket = state.activeVoiceSocket;
  if (!state.voiceRecording || !socket || state.finishInFlight) return;
  state.finishInFlight = true;
  const button = maybe("finishVoiceButton");
  if (button) {
    button.disabled = true;
    setButtonLabel(button, "结束中");
  }
  addStatusLine("正在结束录音；将已识别内容提交。");
  const send = () => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "finish" }));
  };
  if (socket.readyState === WebSocket.CONNECTING) socket.addEventListener("open", send, { once: true });
  else send();
}

function startVoice() {
  if (state.voiceActive) {
    addStatusLine("语音录制已在进行中。");
    return;
  }
  const wasBusy = hasActiveTurn();
  state.voiceActive = true;
  maybe("voiceButton")?.classList.add("active");
  document.querySelectorAll("[data-page-action='voice-start']").forEach((button) => button.classList.add("active"));
  if (maybe("voiceState")) $("voiceState").textContent = "recording";
  syncVoiceControls();
  addStatusLine("正在监听语音输入。");
  addTimeline("voice", wasBusy ? "为运行中的任务追加语音输入" : "已请求语音会话");
  const socket = new WebSocket(wsUrl("/ws/voice"));
  beginStream(socket);
  socket.robonixVoiceMode = "voice";
  state.activeVoiceSocket = socket;
  socket.onopen = () => socket.send(JSON.stringify({
    settings: interactionSettings(wasBusy),
    steer: wasBusy,
    interactionMode: wasBusy ? "steer" : "voice",
    expectedTurnId: wasBusy ? state.activeTurnId : "",
  }));
  wireStream(socket, () => {
    const ownsCapture = state.activeVoiceSocket === socket;
    if (ownsCapture) {
      state.activeVoiceSocket = null;
      state.voiceActive = false;
      state.finishInFlight = false;
      // A socket that dies mid-capture never delivers recording_done, so the
      // flag has to be cleared here too or the control outlives the session.
      state.voiceRecording = false;
    }
    endStream(socket);
    if (ownsCapture) finishVoiceCaptureUi();
    syncVoiceControls();
  }, socket);
  syncVoiceControls();
}

function wireStream(socket, done, voiceSocket = null) {
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "pilot_event") handlePilotEvent(payload.event);
    if (payload.type === "voice_event") handleVoiceEvent(payload.event, voiceSocket);
    if (payload.type === "accepted") addStatusLine("已连接，等待 Robonix 事件。");
    if (payload.type === "status") addTimeline("status", payload.message || "状态更新");
    if (payload.type === "finish_requested") {
      addTimeline(payload.ok ? "voice" : "error", payload.detail || (payload.ok ? "已请求结束录音" : "无法结束录音"));
      // A rejected request leaves the turn recording, so hand the control back
      // rather than stranding the user with a dead "Stopping" button.
      if (!payload.ok) {
        state.finishInFlight = false;
        syncVoiceControls();
      }
    }
    if (payload.type === "error") addMessage("error", payload.error);
    if (payload.type === "done") {
      socket.robonixDone = true;
      socket.close();
    }
  };
  socket.onerror = () => addMessage("error", "数据流异常");
  socket.onclose = done;
}

function handlePilotEvent(event) {
  if (event.kind === "text_chunk" && event.textChunk) {
    appendAgent(event.textChunk);
  } else if (event.kind === "final_text" && event.finalText) {
    finalizeAgent(event.finalText);
  } else if (event.kind === "plan" && event.plan) {
    state.plan = event.plan;
    upsertPlanRecord(event.plan);
    announcePlan(event.plan);
    addTimeline("plan", `实时轮次 ${event.plan.round}: ${planCalls(event.plan).length} 个调用`);
    renderPlan();
    persistCurrentConversation();
    refreshActivePlans();
  } else if (event.kind === "batch_result" && event.batchResult) {
    state.batches.unshift(event.batchResult);
    (event.batchResult.results || []).forEach((result) => {
      if (Number.isFinite(Number(result.nodeIndex))) state.nodeStates[String(result.nodeIndex)] = result;
    });
    updatePlanRecordResult(event.batchResult.planId, (record) => {
      record.batches.unshift(event.batchResult);
      (event.batchResult.results || []).forEach((result) => {
        if (Number.isFinite(Number(result.nodeIndex))) record.nodeStates[String(result.nodeIndex)] = result;
      });
    });
    addTimeline(event.batchResult.anyFailed ? "error" : "result", `第 ${event.batchResult.round} 轮结果`);
    renderPlan();
    persistCurrentConversation();
  } else if (event.kind === "node_state" && event.nodeState) {
    state.nodeStates[String(event.nodeState.nodeIndex)] = event.nodeState;
    updatePlanRecordResult(event.nodeState.planId, (record) => {
      record.nodeStates[String(event.nodeState.nodeIndex)] = event.nodeState;
    });
    addTimeline(event.nodeState.state === "FAILED" ? "error" : "status", `${event.nodeState.opId || `节点 ${event.nodeState.nodeIndex}`} ${event.nodeState.state}`);
    renderPlan();
    persistCurrentConversation();
  } else if (event.kind === "task_state" && event.taskState) {
    state.taskState = event.taskState;
    const taskStatus = String(event.taskState.status || "").trim().toLowerCase();
    if (["in_progress", "running", "planning", "executing"].includes(taskStatus)) {
      state.taskRunning = true;
      state.activePilotSessionId = String(event.sessionId || state.activePilotSessionId || "");
    } else if (["done", "completed", "failed", "cancelled", "canceled", "aborted"].includes(taskStatus)) {
      state.taskRunning = false;
    }
    setBusy(state.activeStreams > 0 || state.taskRunning);
    addTimeline("status", event.taskState.status || event.taskState.goal || "任务状态更新");
    addStatusLine(event.taskState.status || event.taskState.goal || "任务状态已更新。");
    renderPlan();
    persistCurrentConversation();
  } else if (event.kind === "status" && event.status) {
    const turnMatch = String(event.status.message || "").match(/^turn_id=(.+)$/);
    if (turnMatch) {
      state.activeTurnId = turnMatch[1];
      state.activePilotSessionId = String(event.status.sessionId || event.sessionId || "");
      return;
    }
    if ([1, 2].includes(Number(event.status.state))) {
      state.activeTurnId = "";
      state.activePilotSessionId = "";
      state.taskRunning = false;
      setBusy(state.activeStreams > 0);
    }
    addTimeline("status", event.status.message || `状态 ${event.status.state}`);
    if (event.status.message) addStatusLine(event.status.message);
  }
}

function planRecordKey(plan) {
  const planId = String(plan?.planId || "").trim();
  return planId ? `${planId}:${Number(plan?.round || 0)}` : `round:${Number(plan?.round || 0)}`;
}

function upsertPlanRecord(plan) {
  const key = planRecordKey(plan);
  const existing = state.planRecords.find((record) => record.key === key);
  if (existing) {
    existing.plan = plan;
    existing.updatedAt = Date.now();
  } else {
    state.planRecords.unshift({ key, plan, nodeStates: {}, batches: [], updatedAt: Date.now() });
    state.planRecords = state.planRecords.slice(0, 80);
  }
}

function updatePlanRecordResult(planId, update) {
  const id = String(planId || "").trim();
  let record = state.planRecords.find((item) => String(item.plan?.planId || "") === id);
  if (!record && state.plan) {
    upsertPlanRecord(state.plan);
    record = state.planRecords.find((item) => item.key === planRecordKey(state.plan));
  }
  if (!record) return;
  update(record);
  record.updatedAt = Date.now();
}

function handleVoiceEvent(event, sourceSocket = null) {
  const label = event.statusMessage || event.text || event.error || event.kind;
  // Liaison reports the microphone's own lifecycle, so drive the finish
  // control off these rather than off the socket, which stays open through
  // Pilot and TTS long after capture has ended.
  if (event.kind === "recording_started") setVoiceRecording(true);
  else if (["recording_done", "asr_final", "session_done", "error"].includes(event.kind)) {
    setVoiceRecording(false);
  }
  if (event.kind === "asr_final") {
    const mode = sourceSocket?.robonixVoiceMode || "voice";
    addMessage("user", event.text, mode);
    if (sourceSocket && state.activeVoiceSocket === sourceSocket) {
      state.activeVoiceSocket = null;
      state.voiceActive = false;
      state.finishInFlight = false;
      finishVoiceCaptureUi();
      syncVoiceControls();
    }
  } else if (event.kind === "pilot" && event.pilot) {
    handlePilotEvent(event.pilot);
  } else if (event.kind === "tts_started") {
    setTtsAura(true);
    addMessage("status", label || "TTS 播报开始");
    addTimeline("voice", label || "TTS 播报开始");
  } else if (event.kind === "tts_done") {
    setTtsAura(false);
    const skipped = String(label || "").toLowerCase().includes("skipped");
    addMessage(skipped ? "error" : "status", label || "TTS 播报结束");
    addTimeline(skipped ? "error" : "voice", label || "TTS 播报结束");
  } else if (event.kind === "error") {
    addMessage("error", event.error || "语音异常");
  } else {
    addTimeline("voice", label);
  }
}

/// Flip the mic-capture flag and re-sync the controls bound to it. Clearing
/// it also clears any in-flight finish request, since a capture that has
/// ended cannot still be finishing.
function setVoiceRecording(active) {
  if (state.voiceRecording === active) return;
  state.voiceRecording = active;
  if (!active) state.finishInFlight = false;
  syncVoiceControls();
}

function finishVoiceCaptureUi() {
  maybe("voiceButton")?.classList.remove("active");
  document.querySelectorAll("[data-page-action='voice-start']").forEach((button) => button.classList.remove("active"));
  if (maybe("voiceState")) $("voiceState").textContent = "ready";
}

function hasActiveTurn() {
  if (state.activeTurnId) return true;
  const status = String(state.taskState?.status || "").trim().toLowerCase();
  return state.taskRunning || ["in_progress", "running", "planning", "executing"].includes(status);
}

function addMessage(role, text, meta = "", attachments = []) {
  const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.messages.push({ id, role, text, meta, attachments });
  if (role !== "agent") state.activeAgentId = null;
  renderMessages();
  renderSceneAssets();
  persistCurrentConversation(role === "user" ? text : "");
  return id;
}

function addStatusLine(text) {
  const clean = String(text || "").trim();
  if (!clean) return null;
  const last = state.messages[state.messages.length - 1];
  if (last?.role === "status" && last.text === clean) return last.id;
  return addMessage("status", clean, "status");
}

function announcePlan(plan) {
  const round = Number(plan?.round ?? 0);
  if (!round) return;
  const last = state.messages[state.messages.length - 1];
  if (last?.role === "status" && last.planRound === round) return;
  const calls = planCalls(plan);
  const names = calls.map((node) => capabilityLabel(node)).filter(Boolean);
  const preview = names.slice(0, 3).join(", ");
  const suffix = names.length > 3 ? ` +${names.length - 3} more` : "";
  const id = addMessage(
    "status",
    names.length ? `Calling ${preview}${suffix}` : `RTDL plan round ${round}`,
    "RTDL",
  );
  const msg = state.messages.find((item) => item.id === id);
  if (msg) msg.planRound = round;
}

function appendAgent(text) {
  if (!state.activeAgentId) {
    state.activeAgentId = addMessage("agent", "", "Robonix");
  }
  const msg = state.messages.find((item) => item.id === state.activeAgentId);
  if (msg) msg.text += text;
  renderMessages();
  persistCurrentConversation();
}

function finalizeAgent(text) {
  if (!text) {
    state.activeAgentId = null;
    return;
  }
  if (!state.activeAgentId) {
    addMessage("agent", text, "Robonix");
    return;
  }
  const msg = state.messages.find((item) => item.id === state.activeAgentId);
  if (msg) {
    const current = msg.text || "";
    msg.text = mergeFinalText(current, text);
  } else {
    addMessage("agent", text, "Robonix");
  }
  state.activeAgentId = null;
  renderMessages();
  persistCurrentConversation();
}

function mergeFinalText(current, finalText) {
  const currentText = String(current || "");
  const final = String(finalText || "");
  if (!currentText) return final;
  if (!final) return currentText;
  if (final.includes(currentText)) return final;
  if (currentText.includes(final)) return currentText;
  return `${currentText}${currentText.endsWith("\n") ? "" : "\n"}${final}`;
}

function renderMessages() {
  const root = $("messages");
  clear(root);
  if (state.messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "message status";
    empty.textContent = "Ready";
    root.appendChild(empty);
  }
  state.messages.forEach((message) => {
    const el = document.createElement("div");
    el.className = `message ${message.role}`;
    if (message.meta) {
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = message.meta;
      el.appendChild(meta);
    }
    el.appendChild(document.createTextNode(message.text));
    if (message.planRound) {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "message-link";
      action.textContent = "Show RTDL";
      action.addEventListener("click", () => {
        openRtdlHistory();
      });
      el.appendChild(action);
    }
    if (Array.isArray(message.attachments) && message.attachments.length) {
      const images = document.createElement("div");
      images.className = "message-images";
      message.attachments.forEach((item) => {
        const img = document.createElement("img");
        img.src = item.dataUrl;
        img.alt = item.name || "attachment";
        images.appendChild(img);
      });
      el.appendChild(images);
    }
    root.appendChild(el);
  });
  root.scrollTop = root.scrollHeight;
}

function addTimeline(kind, text) {
  state.timeline.unshift({ kind, text, at: new Date().toLocaleTimeString() });
  state.timeline = state.timeline.slice(0, 80);
  renderTimeline();
  persistCurrentConversation();
}

function renderTimeline() {
  setTextAll("[data-event-summary]", String(state.timeline.length));
  setTextAll("[data-current-task-label]", `Current Task: ${currentTaskLabel()}`);
  const rows = state.timeline;
  document.querySelectorAll("[data-event-list]").forEach((root) => {
    clear(root);
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "event-empty";
      empty.textContent = "No task events yet.";
      root.appendChild(empty);
      return;
    }
    rows.forEach((item) => {
      const row = document.createElement("div");
      row.className = "event-row";
      row.textContent = `[${item.at}] ${String(item.kind || "event").toUpperCase()} ${item.text || ""}`;
      root.appendChild(row);
    });
  });
}

function renderPlan() {
  const roots = document.querySelectorAll("[data-plan-tree]");
  roots.forEach((root) => clear(root));
  const records = normalizedPlanRecords();
  const activeRecords = records.filter((record) => recordIsActive(record));
  const latestRecord = activeRecords[0] || null;
  const historyRecords = records.filter((record) => !activeRecords.includes(record));
  const latestCalls = planCalls(latestRecord?.plan).length;
  setTextAll("[data-plan-summary]", latestRecord
    ? `${activeRecords.length} active · plan ${latestRecord.plan.planId || "-"} · round ${latestRecord.plan.round} · ${latestCalls} call(s)`
    : "No RTDL tree is currently executing");
  if (maybe("rtdlHistoryCount")) $("rtdlHistoryCount").textContent = String(historyRecords.length);
  renderGoalPanel();
  renderSceneAssets();
  if (!latestRecord) {
    roots.forEach((root) => {
      const empty = document.createElement("div");
      empty.className = "plan-empty";
      empty.textContent = "No RTDL plan in this session yet.";
      root.appendChild(empty);
    });
  } else {
    roots.forEach((root) => renderPlanRecord(root, latestRecord));
  }
  renderPlanHistory(historyRecords);
  const newest = latestRecord;
  if (!newest) return renderExecutionDetail(null, "PENDING");
  const maps = buildResultMaps(newest);
  const runningIndex = pickRunningIndex(newest.plan, maps.byIndex);
  const activeNode = newest.plan.nodes.find((node) => Number(node.index) === Number(runningIndex))
    || newest.plan.nodes.find((node) => node.call) || newest.plan.nodes[0];
  renderExecutionDetail(activeNode, aggregateNodeStatus(activeNode, newest.plan, maps, runningIndex), resultForNode(activeNode, maps));
}

function normalizedPlanRecords() {
  if (state.planRecords.length) return state.planRecords;
  if (!state.plan) return [];
  return [{ key: planRecordKey(state.plan), plan: state.plan, nodeStates: state.nodeStates || {}, batches: state.batches || [] }];
}

function renderPlanRecord(root, record, onSelect = renderExecutionDetail) {
  const wrapper = document.createElement("section");
  wrapper.className = "plan-record";
  const label = document.createElement("div");
  label.className = "plan-record-label";
  label.textContent = `Plan ${record.plan.planId || "-"} · round ${record.plan.round}`;
  wrapper.appendChild(label);
  const maps = buildResultMaps(record);
  const runningIndex = recordIsActive(record)
    ? pickRunningIndex(record.plan, maps.byIndex)
    : null;
  renderBehaviorTree(wrapper, record.plan, maps, runningIndex, onSelect);
  root.appendChild(wrapper);
}

function renderPlanHistory(records) {
  const root = maybe("rtdlHistoryTrees");
  if (!root) return;
  clear(root);
  records.forEach((record) => renderPlanRecord(root, record, renderHistoryExecutionDetail));
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "plan-empty";
    empty.textContent = "No completed RTDL trees yet.";
    root.appendChild(empty);
  }
}

function renderBehaviorTree(root, plan, resultMaps, runningIndex, onSelect = renderExecutionDetail) {
  const nodes = plan?.nodes || [];
  const nodeStateByIndex = resultMaps.byIndex;
  const byIndex = new Map(nodes.map((node) => [Number(node.index), node]));
  const childSet = new Set();
  nodes.forEach((node) => (node.children || []).forEach((child) => childSet.add(Number(child))));
  const treeRoots = [];
  if (plan.rootIndex !== undefined && byIndex.has(Number(plan.rootIndex))) {
    treeRoots.push(byIndex.get(Number(plan.rootIndex)));
  }
  nodes.forEach((node) => {
    if (!childSet.has(Number(node.index)) && !treeRoots.includes(node)) treeRoots.push(node);
  });
  if (!treeRoots.length && nodes.length) treeRoots.push(nodes[0]);

  treeRoots.forEach((treeRoot, treeIndex) => {
    const status = aggregateNodeStatus(treeRoot, plan, resultMaps, runningIndex);
    const card = document.createElement("div");
    card.className = "bt-tree-card";
    const header = document.createElement("div");
    header.className = "bt-tree-header";
    const title = document.createElement("strong");
    title.textContent = treeRoots.length > 1 ? `Tree ${treeIndex + 1}: ${nodeLabel(treeRoot)}` : nodeLabel(treeRoot);
    const pill = document.createElement("span");
    pill.className = `status ${statusKey(status)}`;
    pill.textContent = displayStatus(status);
    header.append(title, pill);
    const viewport = document.createElement("div");
    viewport.className = "bt-tree-viewport";
    viewport.appendChild(makeBehaviorTreeSvg(treeRoot, plan, resultMaps, runningIndex, onSelect));
    card.append(header, viewport);
    root.appendChild(card);
  });
}

function makeBehaviorTreeSvg(treeRoot, plan, resultMaps, runningIndex, onSelect = renderExecutionDetail) {
  const nodes = plan?.nodes || [];
  const byIndex = new Map(nodes.map((node) => [Number(node.index), node]));
  const nodeStateByIndex = resultMaps.byIndex;
  const nodeW = 104;
  const nodeH = 38;
  const leafGap = 22;
  const levelGap = 72;
  const topPad = 18;
  const sidePad = 18;
  const laid = [];
  let cursor = sidePad;

  const layout = (node, depth) => {
    const children = (node.children || []).map((child) => byIndex.get(Number(child))).filter(Boolean);
    if (!children.length) {
      const pos = { node, depth, x: cursor + nodeW / 2, y: topPad + depth * levelGap };
      cursor += nodeW + leafGap;
      laid.push(pos);
      return pos;
    }
    const childPos = children.map((child) => layout(child, depth + 1));
    const x = (childPos[0].x + childPos[childPos.length - 1].x) / 2;
    const pos = { node, depth, x, y: topPad + depth * levelGap };
    laid.push(pos);
    return pos;
  };

  layout(treeRoot, 0);
  const maxDepth = laid.reduce((m, item) => Math.max(m, item.depth), 0);
  const width = Math.max(220, cursor + sidePad);
  const height = Math.max(88, topPad * 2 + nodeH + maxDepth * levelGap);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "bt-svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));

  const posByIndex = new Map(laid.map((item) => [Number(item.node.index), item]));
  laid.forEach(({ node, x, y }) => {
    const childPositions = (node.children || [])
      .map((child) => posByIndex.get(Number(child)))
      .filter(Boolean);
    if (!childPositions.length) return;
    const y1 = y + nodeH;
    const y2 = childPositions[0].y;
    const branchY = y1 + (y2 - y1) / 2;
    const minX = Math.min(...childPositions.map((child) => child.x));
    const maxX = Math.max(...childPositions.map((child) => child.x));
    const path = document.createElementNS(ns, "path");
    const segments = [`M ${x} ${y1} V ${branchY}`];
    if (childPositions.length > 1) segments.push(`M ${minX} ${branchY} H ${maxX}`);
    childPositions.forEach((child) => segments.push(`M ${child.x} ${branchY} V ${child.y}`));
    path.setAttribute("class", "bt-edge");
    path.setAttribute("d", segments.join(" "));
    svg.appendChild(path);
  });

  const rootPos = posByIndex.get(Number(treeRoot?.index));
  if (rootPos) {
    const entry = document.createElementNS(ns, "circle");
    entry.setAttribute("class", "bt-entry");
    entry.setAttribute("cx", String(rootPos.x));
    entry.setAttribute("cy", "8");
    entry.setAttribute("r", "3");
    svg.appendChild(entry);
    const line = document.createElementNS(ns, "path");
    line.setAttribute("class", "bt-edge");
    line.setAttribute("d", `M ${rootPos.x} 11 L ${rootPos.x} ${rootPos.y}`);
    svg.appendChild(line);
  }

  laid.forEach(({ node, x, y }) => {
    const status = aggregateNodeStatus(node, plan, resultMaps, runningIndex);
    const key = statusKey(status);
    const g = document.createElementNS(ns, "g");
    g.setAttribute("class", `bt-node status-${key}${isRunningNode(node, runningIndex) ? " active" : ""}`);
    g.setAttribute("transform", `translate(${x - nodeW / 2}, ${y})`);
    g.setAttribute("role", "button");
    g.style.cursor = "pointer";
    const title = document.createElementNS(ns, "title");
    title.textContent = `${nodeLabel(node)} · ${capabilityLabel(node)} · ${displayStatus(status)}`;
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("width", String(nodeW));
    rect.setAttribute("height", String(nodeH));
    rect.setAttribute("rx", "5");
    const accent = document.createElementNS(ns, "rect");
    accent.setAttribute("class", "bt-node-accent");
    accent.setAttribute("x", "0");
    accent.setAttribute("y", "4");
    accent.setAttribute("width", "2.5");
    accent.setAttribute("height", String(nodeH - 8));
    accent.setAttribute("rx", "1.25");
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", String(nodeW / 2));
    text.setAttribute("y", "16");
    text.setAttribute("text-anchor", "middle");
    text.textContent = ellipsize(nodeLabel(node), 15);
    const meta = document.createElementNS(ns, "text");
    meta.setAttribute("class", "bt-node-meta");
    meta.setAttribute("x", String(nodeW / 2));
    meta.setAttribute("y", "30");
    meta.setAttribute("text-anchor", "middle");
    meta.textContent = node.call ? ellipsize(compactProvider(node.call), 17) : displayStatus(status);
    g.append(title, rect, accent, text, meta);
    g.addEventListener("click", () => onSelect(node, status, resultForNode(node, resultMaps)));
    svg.appendChild(g);
  });

  return svg;
}

function makePlanRow(node, status, depth, runningIndex) {
  const row = document.createElement("div");
  const key = statusKey(status);
  row.className = `plan-row status-${key}${node.index === runningIndex ? " active" : ""}`;
  row.style.setProperty("--depth", String(Math.min(depth || 0, 6)));
  const rail = document.createElement("span");
  rail.className = "node-rail";
  const body = document.createElement("div");
  body.className = "node-body";
  const top = document.createElement("div");
  top.className = "node-topline";
  const name = document.createElement("strong");
  name.className = "node-name";
  name.textContent = nodeLabel(node);
  const statusEl = document.createElement("span");
  statusEl.className = `status ${key}`;
  statusEl.textContent = displayStatus(status);
  top.append(name, statusEl);
  const meta = document.createElement("div");
  meta.className = "node-meta";
  const type = document.createElement("span");
  type.textContent = `#${node.index} · ${node.kind || "op"}`;
  const provider = document.createElement("span");
  provider.textContent = capabilityLabel(node);
  meta.append(type, provider);
  body.append(top, meta);
  row.append(rail, body);
  row.addEventListener("click", () => renderExecutionDetail(node, status));
  return row;
}

function nodeLabel(node) {
  if (node.call?.name) return node.call.name;
  if (node.opId) return node.opId;
  if (node.description) return node.description;
  const kind = String(node.kind || (node.children?.length ? "sequence" : "leaf"));
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

function capabilityLabel(node) {
  const call = node?.call || {};
  return call.providerId || call.contractId || call.name || "pilot";
}

function compactProvider(call) {
  const provider = String(call?.providerId || "");
  const contract = String(call?.contractId || "");
  const tail = contract ? contract.split("/").pop() : "";
  if (provider && tail) return `${provider}.${tail}`;
  return provider || tail || "call";
}

function formatArgs(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value || {}, null, 2);
}

function computeNodeDepths(plan) {
  const depths = new Map();
  const visit = (index, depth) => {
    if (depths.has(index) && depths.get(index) <= depth) return;
    depths.set(index, depth);
    const node = plan.nodes.find((item) => item.index === index);
    (node?.children || []).forEach((child) => visit(child, depth + 1));
  };
  visit(Number(plan.rootIndex || 0), 0);
  plan.nodes.forEach((node) => {
    if (!depths.has(node.index)) depths.set(node.index, 0);
  });
  return depths;
}

function planForestNodes(plan) {
  const byIndex = new Map((plan?.nodes || []).map((node) => [Number(node.index), node]));
  const seen = new Set();
  const out = [];
  const emit = (index, depth) => {
    const idx = Number(index);
    const node = byIndex.get(idx);
    if (!node || seen.has(idx)) return;
    seen.add(idx);
    out.push({ node, depth });
    (node.children || []).forEach((child) => emit(child, depth + 1));
  };
  if (plan && plan.rootIndex !== undefined) emit(plan.rootIndex, 0);
  (plan?.nodes || []).forEach((node) => emit(node.index, 0));
  return out;
}

function aggregateNodeStatus(node, plan, resultMaps, runningIndex) {
  const own = resultForNode(node, resultMaps);
  if (own?.state) {
    if (String(own.state).toUpperCase() === "RUNNING" && runningIndex === null) return "ENDED";
    return own.state;
  }
  if (isRunningNode(node, runningIndex)) return "RUNNING";
  const children = (node?.children || [])
    .map((idx) => (plan?.nodes || []).find((item) => Number(item.index) === Number(idx)))
    .filter(Boolean);
  if (!children.length) return "PENDING";
  const childStatuses = children.map((child) => statusKey(aggregateNodeStatus(child, plan, resultMaps, runningIndex)));
  if (childStatuses.includes("failed")) return "FAILED";
  if (childStatuses.includes("running")) return "RUNNING";
  if (childStatuses.length && childStatuses.every((s) => s === "success")) return "SUCCEEDED";
  return "PENDING";
}

function ellipsize(text, max) {
  const value = String(text || "");
  return value.length <= max ? value : `${value.slice(0, Math.max(1, max - 1))}…`;
}

function pickRunningIndex(plan, nodeStateByIndex) {
  const callable = plan.nodes.filter((node) => node.call);
  const explicitRunning = plan.nodes.find((node) => nodeStateByIndex.get(node.index)?.state === "RUNNING");
  if (explicitRunning) return explicitRunning.index;
  const firstPending = callable.find((node) => !nodeStateByIndex.has(node.index));
  return firstPending?.index ?? callable.at(-1)?.index ?? plan.rootIndex ?? 0;
}

function isRunningNode(node, runningIndex) {
  return runningIndex !== null
    && runningIndex !== undefined
    && Number(node?.index) === Number(runningIndex);
}

function nodeStatus(node, nodeStateByIndex, runningIndex) {
  const result = nodeStateByIndex.get(node?.index);
  if (result?.state) return result.state;
  if (node.index === runningIndex) return "RUNNING";
  if (!node.call && (node.children || []).length) {
    if (node.children.some((child) => child === runningIndex)) return "RUNNING";
    return "PENDING";
  }
  return "PENDING";
}

function durationForNode(node, status) {
  const result = nodeResult(node);
  const value = result?.durationMs ?? result?.duration_ms ?? result?.elapsedMs ?? result?.elapsed_ms;
  if (Number.isFinite(Number(value))) return `${(Number(value) / 1000).toFixed(2)}s`;
  const key = statusKey(status);
  if (key === "pending") return "-";
  if (key === "running") return "running";
  return "done";
}

function startedForNode(node, status) {
  const result = nodeResult(node);
  const value = result?.startedAt || result?.started_at || result?.startTime || result?.start_time;
  if (!value) return statusKey(status) === "pending" ? "-" : "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function statusKey(status) {
  const raw = String(status || "pending").toLowerCase();
  if (raw === "succeeded" || raw === "success" || raw === "done" || raw === "completed") return "success";
  if (["failed", "failure", "error", "canceled", "cancelled", "timeout", "aborted"].includes(raw)) return "failed";
  if (raw === "running" || raw === "in_progress" || raw === "active") return "running";
  if (raw === "ended" || raw === "inactive") return "ended";
  return "pending";
}

function recordHasTerminalBatch(record) {
  return Array.isArray(record?.batches) && record.batches.length > 0;
}

function recordIsActive(record) {
  if (!record?.plan || recordHasTerminalBatch(record)) return false;
  if (!state.executorPlansReady) return record === normalizedPlanRecords()[0];
  const planId = String(record.plan.planId || "");
  if (state.executorPlanIds.has(planId)) return true;
  return Number(state.executorMissingPolls.get(planId) || 0) < 2;
}

async function refreshActivePlans() {
  const atlas = buildAtlasEndpoint(maybe("robotHost")?.value, maybe("atlasPort")?.value);
  if (!atlas) {
    state.executorPlansReady = false;
    state.executorPlans = [];
    state.executorPlanIds = new Set();
    renderActivePlans("Set Robot Host first.");
    return;
  }
  const settings = { ...collectSettings(), atlasEndpoint: atlas };
  const result = await fetch("/api/executor/active-plans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings }),
  }).then((response) => response.json()).catch((error) => ({
    available: false,
    count: 0,
    plans: [],
    error: String(error),
  }));
  if (!result.available) {
    renderActivePlans(result.error || "Executor query unavailable.");
    return;
  }
  state.executorPlansReady = true;
  state.executorPlans = Array.isArray(result.plans) ? result.plans : [];
  state.executorPlanIds = new Set(state.executorPlans.map((plan) => String(plan.planId || "")));
  normalizedPlanRecords().forEach((record) => {
    const planId = String(record.plan?.planId || "");
    if (!planId || state.executorPlanIds.has(planId) || recordHasTerminalBatch(record)) {
      state.executorMissingPolls.set(planId, 0);
      return;
    }
    state.executorMissingPolls.set(planId, Number(state.executorMissingPolls.get(planId) || 0) + 1);
  });
  renderActivePlans();
  renderPlan();
}

function renderActivePlans(error = "") {
  const root = maybe("activeRtdlList");
  const count = maybe("activeRtdlCount");
  const summary = maybe("activeRtdlSummary");
  const modalSummary = maybe("activeRtdlModalSummary");
  if (!root || !count) return;
  clear(root);
  if (error) {
    count.textContent = "unavailable";
    if (summary) summary.textContent = "Executor state unavailable";
    if (modalSummary) modalSummary.textContent = "执行器实时查询失败";
    const row = document.createElement("div");
    row.className = "active-rtdl-empty error";
    row.textContent = error;
    root.appendChild(row);
    return;
  }
  const planCount = state.executorPlans.length;
  count.textContent = String(planCount);
  if (summary) summary.textContent = planCount ? `${planCount} 个运行中 · 打开实时工作区` : "当前无运行中的计划";
  if (modalSummary) modalSummary.textContent = `执行器上报了 ${planCount} 个实时计划`;
  if (!state.executorPlans.length) {
    const row = document.createElement("div");
    row.className = "active-rtdl-empty";
    row.textContent = "执行器报告当前没有活跃的 RTDL 计划。";
    root.appendChild(row);
    return;
  }
  state.executorPlans.forEach((plan) => {
    const card = document.createElement("article");
    card.className = `active-rtdl-card${plan.cancelled ? " canceling" : ""}`;
    const header = document.createElement("header");
    header.className = "active-rtdl-card-header";
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = plan.description || `Plan ${plan.planId}`;
    const meta = document.createElement("span");
    const runningOps = (plan.ops || []).filter((op) => op.state === "running").length;
    meta.textContent = `plan ${plan.planId} · ${runningOps}/${plan.opCount} running`;
    body.append(title, meta);
    const statePill = document.createElement("span");
    statePill.className = `status ${plan.cancelled ? "ended" : "running"}`;
    statePill.textContent = plan.cancelled ? "CANCELING" : "RUNNING";
    header.append(body, statePill);
    card.appendChild(header);

    const ops = document.createElement("div");
    ops.className = "active-rtdl-ops";
    const planOps = Array.isArray(plan.ops) ? plan.ops : [];
    if (!planOps.length) {
      const empty = document.createElement("span");
      empty.className = "active-rtdl-empty";
      empty.textContent = "No operation details reported.";
      ops.appendChild(empty);
    } else {
      planOps.forEach((op) => {
        const opRow = document.createElement("div");
        opRow.className = "active-rtdl-op";
        const opMain = document.createElement("div");
        opMain.className = "active-rtdl-op-main";
        const opTitle = document.createElement("strong");
        opTitle.textContent = op.description || `Operation ${op.op_id || "-"}`;
        const opMeta = document.createElement("span");
        opMeta.textContent = `op ${op.op_id || "-"} · ${op.kind || "do"}`;
        opMain.append(opTitle, opMeta);
        const target = document.createElement("div");
        target.className = "active-rtdl-op-target";
        target.textContent = op.provider_id || op.contract_id
          ? `${op.provider_id || "?"} · ${op.contract_id || "operator"}`
          : "operator node";
        const opState = document.createElement("span");
        const stateName = String(op.state || "pending").toLowerCase();
        opState.className = `status ${stateName}`;
        opState.textContent = stateName.toUpperCase();
        opRow.append(opMain, target, opState);
        ops.appendChild(opRow);
      });
    }
    card.appendChild(ops);
    root.appendChild(card);
  });
}

function displayStatus(status) {
  return statusKey(status).toUpperCase();
}

function renderExecutionDetail(node, status, nodeState = null) {
  if (!maybe("activeProvider")) return;
  if (maybe("executionDetailTitle")) $("executionDetailTitle").textContent = node ? "Node detail" : "Node detail";
  if (!node) {
    $("activeProvider").textContent = "-";
    $("activeStarted").textContent = "-";
    $("activeDuration").textContent = "-";
    $("activeArgs").textContent = "Select an RTDL node to inspect its arguments and result.";
    return;
  }
  $("activeProvider").textContent = detailProvider(node);
  $("activeStarted").textContent = node ? startedForNode(node, status) : "-";
  $("activeDuration").textContent = node ? durationForNode(node, status) : "-";
  $("activeArgs").textContent = formatArgs(detailPayload(node, status, nodeState));
}

function renderHistoryExecutionDetail(node, status, nodeState = null) {
  if (!maybe("historyActiveProvider")) return;
  if (maybe("historyExecutionDetailTitle")) {
    $("historyExecutionDetailTitle").textContent = node ? nodeLabel(node) : "Node detail";
  }
  if (!node) {
    $("historyActiveProvider").textContent = "-";
    $("historyActiveStarted").textContent = "-";
    $("historyActiveDuration").textContent = "-";
    $("historyActiveArgs").textContent = "Select an RTDL node to inspect its arguments and result.";
    return;
  }
  $("historyActiveProvider").textContent = detailProvider(node);
  $("historyActiveStarted").textContent = startedForNode(node, status);
  $("historyActiveDuration").textContent = durationForNode(node, status);
  $("historyActiveArgs").textContent = formatArgs(detailPayload(node, status, nodeState));
}

function buildResultMaps(record = null) {
  const byIndex = new Map();
  const byCallId = new Map();
  const add = (result) => {
    if (!result) return;
    const idx = Number(result.nodeIndex);
    if (Number.isFinite(idx)) byIndex.set(idx, result);
    const callId = result.leafResult?.callId || result.callId;
    if (callId) byCallId.set(String(callId), result);
  };
  Object.values(record?.nodeStates || state.nodeStates || {}).forEach(add);
  (record?.batches || state.batches).forEach((batch) => (batch.results || []).forEach(add));
  return { byIndex, byCallId };
}

function resultForNode(node, maps = buildResultMaps()) {
  if (!node) return null;
  const callId = node.call?.callId ? String(node.call.callId) : "";
  if (callId && maps.byCallId?.has(callId)) return maps.byCallId.get(callId);
  const indexed = maps.byIndex?.get(Number(node.index)) || null;
  if (!indexed) return null;
  if (!node.call) return indexed.leafResult ? { ...indexed, leafResult: null } : indexed;
  const resultCallId = indexed.leafResult?.callId || indexed.callId || "";
  return !resultCallId || String(resultCallId) === callId ? indexed : null;
}

function nodeResult(node) {
  return resultForNode(node);
}

function detailProvider(node) {
  if (!node) return "-";
  if (!node.call) return `${node.kind || "op"}${node.opId ? ` / ${node.opId}` : ""}`;
  return node.call.providerId || node.call.contractId || node.call.name || "call";
}

function detailPayload(node, status, nodeState) {
  if (!node) return {};
  if (!node.call) {
    return {
      kind: node.kind || "op",
      opId: node.opId || "",
      description: node.description || "",
      status: displayStatus(status),
      children: node.children || [],
    };
  }
  return {
    call: {
      callId: node.call.callId || "",
      providerId: node.call.providerId || "",
      contractId: node.call.contractId || "",
      name: node.call.name || "",
      args: node.call.args || {},
    },
    result: nodeState?.leafResult || null,
    state: nodeState?.state || displayStatus(status),
  };
}

function planCalls(plan) {
  return (plan?.nodes || []).filter((node) => node.call);
}

function activePlanNode() {
  const record = normalizedPlanRecords().find((item) => recordIsActive(item));
  if (!record) return null;
  const maps = buildResultMaps(record);
  const runningIndex = pickRunningIndex(record.plan, maps.byIndex);
  return record.plan.nodes.find((node) => node.index === runningIndex)
    || record.plan.nodes.find((node) => node.call)
    || null;
}

function currentExecutionContext() {
  if (state.executorPlansReady) {
    for (const plan of state.executorPlans) {
      const runningOp = (plan.ops || []).find((op) => String(op.state || "").toLowerCase() === "running") || null;
      const record = normalizedPlanRecords().find(
        (item) => String(item.plan?.planId || "") === String(plan.planId || ""),
      );
      const node = runningOp && record
        ? record.plan.nodes.find((item) => String(item.opId || "") === String(runningOp.op_id || runningOp.opId || "")) || null
        : null;
      return {
        source: runningOp ? "Executor verified" : "Executor plan verified",
        plan,
        op: runningOp,
        node: node || (record ? activeNodeForRecord(record) : null),
      };
    }
    return null;
  }
  const node = activePlanNode();
  return node ? { source: "Pilot stream estimate", plan: null, op: null, node } : null;
}

function activeNodeForRecord(record) {
  const maps = buildResultMaps(record);
  const runningIndex = pickRunningIndex(record.plan, maps.byIndex);
  return record.plan.nodes.find((node) => Number(node.index) === Number(runningIndex))
    || record.plan.nodes.find((node) => node.call)
    || null;
}

function appendGoalField(root, label, value) {
  const row = document.createElement("div");
  const key = document.createElement("span");
  const content = document.createElement("strong");
  key.textContent = label;
  content.textContent = value || "-";
  row.append(key, content);
  root.appendChild(row);
}

function renderGoalPanel() {
  const task = state.taskState || {};
  const context = currentExecutionContext();
  const active = context?.node || null;
  const taskText = task.goal || task.task || firstUserMessage() || "waiting for task";
  const status = task.status || (context ? "executing" : "idle");
  if (maybe("goalLine")) $("goalLine").textContent = `${status}: ${taskText}`;
  document.querySelectorAll("[data-goal-preview]").forEach((goal) => {
    clear(goal);
    const card = document.createElement("div");
    card.className = "goal-card";
    const source = document.createElement("span");
    source.className = `goal-source${context?.source === "Executor verified" ? " verified" : ""}`;
    source.textContent = context?.source || (state.executorPlansReady ? "Executor verified" : "Executor unavailable");
    const title = document.createElement("strong");
    title.textContent = active?.call?.name
      || context?.op?.description
      || context?.plan?.description
      || "No active Executor call";
    card.append(source, title);
    if (context) {
      const fields = document.createElement("div");
      fields.className = "goal-call-grid";
      const providerId = active?.call?.providerId || context.op?.provider_id || context.op?.providerId || "-";
      const contractId = active?.call?.contractId || context.op?.contract_id || context.op?.contractId || "-";
      appendGoalField(fields, "Provider", providerId);
      appendGoalField(fields, "Contract", contractId);
      appendGoalField(
        fields,
        "Operation",
        active?.call?.name || (contractId !== "-" ? contractId.split("/").pop() : "") || context.op?.description || nodeLabel(active || {}),
      );
      appendGoalField(
        fields,
        "Plan / node",
        `${context.plan?.planId || "-"} / ${context.op?.op_id || context.op?.opId || active?.opId || active?.index || "-"}`,
      );
      card.appendChild(fields);
    } else {
      const empty = document.createElement("span");
      empty.textContent = state.executorPlansReady
        ? "Executor reports no running RTDL plan."
        : "Connect to Executor to read the authoritative running call.";
      card.appendChild(empty);
    }
    const target = goalSummary(active);
    if (target) {
      const detail = document.createElement("pre");
      detail.className = "goal-json";
      detail.textContent = target;
      card.appendChild(detail);
    }
    goal.appendChild(card);
  });
}

function goalSummary(node) {
  const args = node?.call?.args;
  if (!args || typeof args !== "object") return "";
  const keys = ["goal", "object_id", "map_id", "target", "query", "text"];
  const out = {};
  keys.forEach((key) => {
    if (args[key] !== undefined) out[key] = args[key];
  });
  return Object.keys(out).length ? formatArgs(out) : "";
}

function currentTaskLabel() {
  const text = firstUserMessage();
  if (!text) return "idle";
  return text.length > 40 ? `${text.slice(0, 37)}...` : text;
}

async function refreshVoiceFinishSupport() {
  // Older liaisons never registered robonix/system/liaison/voice/finish, so
  // absence just means "not upgraded yet" -- keep the button hidden rather
  // than let a click fail with a raw gRPC UNIMPLEMENTED error.
  const result = await fetch("/api/voice/finish-supported", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings() }),
  }).then((r) => r.json()).catch(() => ({ supported: false }));
  state.voiceFinishSupported = Boolean(result.supported);
  syncVoiceControls();
}

async function refreshSystem() {
  const settings = collectSettings();
  const atlas = settings.atlasEndpoint || buildAtlasEndpoint(settings.robotHost, settings.atlasPort);
  if (!atlas) {
    renderSystem({ error: "请先配置机器人主机和 Atlas 端口。", summary: { state: "offline" }, requiredContracts: [], providers: [] });
    return;
  }
  const data = await fetch(`/api/system?atlas=${encodeURIComponent(atlas)}`).then((r) => r.json()).catch((error) => ({ error: String(error) }));
  const entry = state.agents[state.activeAgentId];
  if (entry) {
    entry.snapshot = data;
    entry.status = data.error ? "offline" : (data.summary?.state || "online");
  }
  renderSystem(data);
}

function renderSystem(data) {
  const summary = data.summary || {};
  const stateLabel = data.error ? "offline" : summary.state || "unknown";
  const online = !data.error;
  const refreshSystem = maybe("refreshSystem");
  if (refreshSystem) {
    const conn = maybe("connectionState");
    if (conn) conn.textContent = stateLabel;
    refreshSystem.classList.toggle("offline", !online);
    refreshSystem.classList.toggle("online", online);
  }
  const connectNow = maybe("connectNow");
  if (connectNow) {
    connectNow.textContent = online ? "已连接" : "连接";
    connectNow.classList.toggle("connected", online);
    connectNow.title = online ? "Atlas 已连通" : "检查 Atlas 连接";
  }
  if (maybe("metricState")) $("metricState").textContent = stateLabel;
  if (maybe("metricActive")) $("metricActive").textContent = String(summary.active || 0);
  if (maybe("metricErrors")) $("metricErrors").textContent = String(summary.errors || 0);
  renderRobotState(data);

  const contractRoot = maybe("contractList");
  if (!contractRoot) return;
  clear(contractRoot);
  (data.requiredContracts || []).forEach((item) => {
    const row = document.createElement("div");
    row.className = "contract-row";
    const label = document.createElement("strong");
    label.textContent = item.label;
    const status = document.createElement("span");
    status.className = item.available ? "ok" : "warn";
    status.textContent = item.available ? item.providers.join(", ") : "missing";
    row.append(label, status);
    contractRoot.appendChild(row);
  });

  const providerRoot = maybe("providerList");
  if (!providerRoot) return;
  clear(providerRoot);
  if (data.error) {
    const row = document.createElement("div");
    row.className = "provider-row";
    row.textContent = data.error;
    providerRoot.appendChild(row);
    return;
  }
  (data.providers || []).forEach((provider) => {
    const row = document.createElement("div");
    row.className = "provider-row";
    const title = document.createElement("strong");
    title.textContent = provider.id;
    const meta = document.createElement("span");
    meta.textContent = `${provider.kind}  ${provider.state}  ${provider.capabilities.length} cap(s)`;
    row.append(title, meta);
    providerRoot.appendChild(row);
  });
}

function renderRobotState(data) {
  if (!document.querySelector("[data-robot-state-list]")) return;
  const contracts = data.requiredContracts || [];
  const summary = data.summary || {};
  const recording = maybe("voiceState") ? $("voiceState").textContent === "recording" : false;
  const audioReady = contractAvailable(contracts, "Speaker") || contractAvailable(contracts, "TTS");
  const rows = [
    { label: "底盘", icon: "B", ok: contractAvailable(contracts, "Executor") || contractAvailable(contracts, "Liaison submit"), status: "正常", value: "0.00 m/s", source: "mock" },
    { label: "机械臂", icon: "A", ok: summary.errors === 0, status: "正常", value: "空闲", source: "mock" },
    { label: "头部/相机", icon: "C", ok: true, status: "正常", value: "跟踪中", source: "mock" },
    { label: "电池", icon: "P", ok: true, status: "86%", value: "2h 14m", source: "mock", battery: 86 },
    { label: "定位", icon: "L", ok: !data.error, status: "正常", value: "0.04 m", source: "mock", separated: true },
    { label: "导航", icon: "N", ok: contractAvailable(contracts, "Executor"), status: state.busy ? "运动中" : "就绪", value: state.busy ? "0.32 m" : "0.00 m", source: "derived", warn: state.busy },
    { label: "音频输入", icon: "M", ok: contractAvailable(contracts, "Mic") || contractAvailable(contracts, "ASR"), status: recording ? "监听中" : "待机", value: "", source: "real", wave: recording },
    { label: "音频输出", icon: "S", ok: audioReady, status: state.ttsPlaying ? "播报中" : "就绪", value: "", source: "real", wave: state.ttsPlaying },
    { label: "连接", icon: "O", ok: !data.error, status: data.error ? "离线" : "在线", value: "", source: "real", separated: true },
    { label: "安全", icon: "!", ok: summary.errors === 0, status: summary.errors ? `${summary.errors} 个错误` : "正常", value: "", source: "derived", danger: summary.errors > 0 },
  ];
  setTextAll("[data-robot-mode]", data.error ? "离线" : state.busy ? "执行中" : "就绪");
  document.querySelectorAll("[data-robot-state-list]").forEach((root) => {
    clear(root);
    rows.forEach((item) => {
      const row = document.createElement("div");
      row.className = `robot-state-row${item.separated ? " separated" : ""}`;
      row.title = `source: ${item.source}`;
      const icon = document.createElement("span");
      icon.className = `state-icon ${item.danger ? "danger" : item.ok ? "ok" : "warn"}`;
      icon.textContent = item.icon;
      const label = document.createElement("strong");
      label.textContent = item.label;
      const stateEl = document.createElement("span");
      stateEl.className = item.danger ? "bad" : item.warn ? "warn" : item.ok ? "ok" : "warn";
      stateEl.textContent = item.status;
      const value = document.createElement("span");
      value.textContent = item.value;
      row.append(icon, label, stateEl, value);
      root.appendChild(row);
      if (item.battery) {
        const bar = document.createElement("div");
        bar.className = "battery-meter";
        const fill = document.createElement("span");
        fill.style.width = `${item.battery}%`;
        bar.appendChild(fill);
        root.appendChild(bar);
      }
      if (item.wave) {
        const wave = document.createElement("span");
        wave.className = "audio-wave";
        value.appendChild(wave);
      }
    });
  });
}

function contractAvailable(contracts, label) {
  const found = contracts.find((item) => item.label === label);
  return Boolean(found?.available);
}

function renderSceneAssets() {
  renderObjectTable();
}

function latestNavigationGoal() {
  const nodes = state.plan?.nodes || [];
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    const call = nodes[i].call;
    if (!call) continue;
    const contract = String(call.contractId || "");
    const name = String(call.name || "");
    if (!contract.includes("navigation/navigate") && !name.includes("navigate")) continue;
    const goal = call.args?.goal;
    const pose = goal?.pose;
    const position = pose?.position;
    const orientation = pose?.orientation;
    if (!position) continue;
    const yaw = yawFromQuaternion(orientation);
    return {
      x: Number(position.x),
      y: Number(position.y),
      yaw: Number.isFinite(yaw) ? yaw : 0,
    };
  }
  return null;
}

function yawFromQuaternion(q) {
  if (!q) return 0;
  const z = Number(q.z || 0);
  const w = Number(q.w || 1);
  return 2 * Math.atan2(z, w);
}

function formatMeters(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(2)} m` : "-";
}

function formatRadians(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(2)} rad` : "-";
}

function renderObjectTable() {
  document.querySelectorAll("[data-object-table]").forEach((root) => {
    clear(root);
  });
}

function latestImageAttachment() {
  if (state.attachments.length) return state.attachments[state.attachments.length - 1];
  for (let index = state.messages.length - 1; index >= 0; index -= 1) {
    const attachments = state.messages[index].attachments || [];
    const image = attachments.find((item) => String(item.mediaType || "").startsWith("image/"));
    if (image) return image;
  }
  return null;
}

/// Make `base` unique among the other conversations by appending a counter,
/// so a sidebar of identically-named chats stays tellable apart. Only the
/// conversation being written is renamed; existing titles are left alone.
///
/// An existing " (n)" is stripped before counting, so deriving a name from an
/// already-numbered one yields "123 (3)" rather than compounding it into
/// "123 (2) (2)".
function uniqueConversationTitle(base, selfId) {
  const stem = String(base).replace(/\s*\(\d+\)$/, "").trim() || "Untitled chat";
  const taken = new Set(
    state.history.filter((item) => item.id !== selfId).map((item) => item.title)
  );
  if (!taken.has(stem)) return stem;
  for (let suffix = 2; ; suffix += 1) {
    const candidate = `${stem} (${suffix})`;
    if (!taken.has(candidate)) return candidate;
  }
}

function persistCurrentConversation(titleHint = "", force = false) {
  const hasContent = state.sessionTitle || state.messages.length || state.timeline.length || state.plan || state.planRecords.length || state.batches.length || Object.keys(state.nodeStates || {}).length;
  if (!hasContent && !force) return;
  const existingIndex = state.history.findIndex((item) => item.id === state.sessionId);
  const existing = existingIndex >= 0 ? state.history[existingIndex] : null;
  const baseTitle = state.sessionTitle || existing?.title || titleHint || firstUserMessage() || "Untitled chat";
  const title = uniqueConversationTitle(baseTitle, state.sessionId);
  state.sessionTitle = title;
  const conversation = {
    id: state.sessionId,
    title,
    updatedAt: Date.now(),
    messages: state.messages.map((item) => ({ ...item })),
    timeline: state.timeline.map((item) => ({ ...item })),
    plan: state.plan,
    planRecords: state.planRecords,
    batches: state.batches,
    nodeStates: state.nodeStates,
  };
  // Update in place. Hoisting the current conversation to the front on every
  // save re-sorted the sidebar just from visiting a chat, so rows moved out
  // from under the pointer mid-click. New conversations still go on top.
  if (existingIndex >= 0) state.history[existingIndex] = conversation;
  else state.history = [conversation, ...state.history].slice(0, 30);
  saveConversations();
  renderHistory();
}

function renderHistory() {
  const root = $("historyList");
  if (!root) return;
  clear(root);
  if (!state.history.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "No saved conversations yet.";
    root.appendChild(empty);
    return;
  }
  state.history.forEach((item) => {
    const row = document.createElement("div");
    row.className = `history-item${item.id === state.sessionId ? " active" : ""}`;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "history-open";
    open.title = item.title;
    const title = document.createElement("strong");
    title.textContent = item.title || "Untitled chat";
    const meta = document.createElement("span");
    meta.textContent = formatConversationTime(item.updatedAt);
    open.append(title, meta);
    open.addEventListener("click", () => openConversation(item.id));
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "history-rename";
    rename.title = "Rename conversation";
    rename.setAttribute("aria-label", `Rename ${item.title || "conversation"}`);
    rename.textContent = "Rename";
    rename.addEventListener("click", (event) => {
      event.stopPropagation();
      renameConversation(item.id);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "history-delete";
    remove.title = "Delete conversation";
    remove.setAttribute("aria-label", `Delete ${item.title || "conversation"}`);
    remove.textContent = "Delete";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteConversation(item.id);
    });
    row.append(open, rename, remove);
    root.appendChild(row);
  });
}

function renameConversation(sessionId) {
  // window.prompt blocks the event loop, which would stall a live event
  // stream, so renaming waits. Say so rather than ignoring the click.
  if (state.busy) {
    addStatusLine("任务运行中无法重命名会话。");
    return;
  }
  if (sessionId === state.sessionId) persistCurrentConversation("", true);
  const conversation = state.history.find((item) => item.id === sessionId);
  const currentTitle = conversation?.title || state.sessionTitle || firstUserMessage() || "未命名会话";
  const nextTitle = window.prompt("重命名会话", currentTitle);
  if (nextTitle === null) return;
  const trimmed = nextTitle.trim();
  if (!trimmed) return;
  const title = uniqueConversationTitle(trimmed, sessionId);
  if (sessionId === state.sessionId) {
    state.sessionTitle = title;
    $("promptTitle").textContent = title;
    renderSessionChip();
  }
  if (conversation) {
    // Renaming must not reorder the list either -- see persistCurrentConversation.
    conversation.title = title;
    conversation.updatedAt = Date.now();
  } else if (sessionId === state.sessionId) {
    persistCurrentConversation(title, true);
  }
  saveConversations();
  renderHistory();
}

function deleteConversation(sessionId) {
  state.history = state.history.filter((item) => item.id !== sessionId);
  saveConversations();
  if (sessionId === state.sessionId) {
    state.sessionId = getSessionId();
    rememberLastSession(state.sessionId);
    forgetActiveTurn();
    state.sessionTitle = "";
    state.messages = [];
    state.timeline = [];
    state.plan = null;
    state.planRecords = [];
    state.batches = [];
    state.nodeStates = {};
    state.activeAgentId = null;
    $("promptTitle").textContent = "What should Robonix do?";
    renderSessionChip();
    renderMessages();
    renderTimeline();
    renderPlan();
    renderSceneAssets();
  }
  renderHistory();
}

function clearHistory() {
  state.history = [];
  saveConversations();
  state.sessionId = getSessionId();
  rememberLastSession(state.sessionId);
  forgetActiveTurn();
  state.sessionTitle = "";
  state.messages = [];
  state.timeline = [];
  state.plan = null;
  state.planRecords = [];
  state.batches = [];
  state.nodeStates = {};
  state.activeAgentId = null;
  $("promptTitle").textContent = "What should Robonix do?";
  renderMessages();
  renderTimeline();
  renderPlan();
  renderSceneAssets();
  renderHistory();
}

function openConversation(sessionId) {
  if (sessionId === state.sessionId) return;
  // A running turn streams its events into whatever conversation is on
  // screen, so switching mid-flight would file another session's replies
  // here. Refuse, but say why -- returning silently reads as a dead list.
  if (state.busy) {
    addStatusLine("当前会话仍有任务在运行，请先中止任务再切换会话。");
    return;
  }
  persistCurrentConversation();
  const conversation = state.history.find((item) => item.id === sessionId);
  if (!conversation) return;
  state.sessionId = conversation.id;
  rememberLastSession(conversation.id);
  state.sessionTitle = conversation.title || "";
  forgetActiveTurn();
  state.messages = (conversation.messages || []).map((item) => ({ ...item }));
  state.timeline = (conversation.timeline || []).map((item) => ({ ...item }));
  state.plan = conversation.plan || null;
  state.planRecords = conversation.planRecords || [];
  state.batches = conversation.batches || [];
  state.nodeStates = conversation.nodeStates || {};
  state.activeAgentId = null;
  $("promptTitle").textContent = conversation.title || "What should Robonix do?";
  renderSessionChip();
  $("taskInput").value = "";
  autoGrowInput();
  renderMessages();
  renderTimeline();
  renderPlan();
  renderSceneAssets();
  renderHistory();
}

function firstUserMessage() {
  const user = state.messages.find((item) => item.role === "user" && item.text);
  return user ? user.text : "";
}

function formatConversationTime(ms) {
  if (!ms) return "";
  const date = new Date(ms);
  return date.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function routeOption(select, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.appendChild(option);
}

function renderAudioRouteProviders(route) {
  const mic = maybe("micNodeId");
  const speaker = maybe("speakerNodeId");
  if (!mic || !speaker) return;
  const savedMic = state.settings.micNodeId || "";
  const savedSpeaker = state.settings.speakerNodeId || "";
  clear(mic);
  clear(speaker);
  routeOption(mic, "", "Select input primitive");
  routeOption(speaker, "", "Select output primitive");
  (route.micProviders || []).forEach((provider) => {
    routeOption(mic, provider.id, provider.namespace ? `${provider.id} (${provider.namespace})` : provider.id);
  });
  (route.speakerProviders || []).forEach((provider) => {
    routeOption(speaker, provider.id, provider.namespace ? `${provider.id} (${provider.namespace})` : provider.id);
  });
  const micAvailable = (route.micProviders || []).some((provider) => provider.id === savedMic);
  const speakerAvailable = (route.speakerProviders || []).some((provider) => provider.id === savedSpeaker);
  if (savedMic && !micAvailable) routeOption(mic, savedMic, `${savedMic} (unavailable)`);
  if (savedSpeaker && !speakerAvailable) routeOption(speaker, savedSpeaker, `${savedSpeaker} (unavailable)`);
  mic.value = savedMic || "";
  speaker.value = savedSpeaker || "";
}

function renderAudioRouteDevices(side, result) {
  const select = maybe(side === "mic" ? "micDeviceId" : "speakerDeviceId");
  if (!select) return;
  const saved = side === "mic" ? state.settings.micDeviceId || "" : state.settings.speakerDeviceId || "";
  const current = side === "mic" ? result.currentInputId : result.currentOutputId;
  const wantedKind = side === "mic" ? "input" : "output";
  clear(select);
  routeOption(select, "", "OS default");
  (result.devices || [])
    .filter((device) => device.kind === wantedKind || device.kind === "duplex")
    .forEach((device) => {
      const suffix = [device.channels ? `${device.channels} ch` : "", device.note || ""].filter(Boolean).join(", ");
      routeOption(select, device.id, suffix ? `${device.name} (${suffix})` : device.name || device.id);
    });
  const devices = result.devices || [];
  const target = devices.some((device) => device.id === saved) ? saved : (current || "");
  select.value = target;
  renderBridgeDeviceReadout(side, result, target);
}

function renderBridgeDeviceReadout(side, result, selectedId) {
  const provider = maybe(side === "mic" ? "micNodeId" : "speakerNodeId")?.value || "";
  const target = maybe(side === "mic" ? "bridgeInputDevice" : "bridgeOutputDevice");
  if (!target) return;
  if (provider !== "audio_client_bridge") {
    target.textContent = "Not using client bridge";
    return;
  }
  const device = (result.devices || []).find((entry) => entry.id === selectedId);
  target.textContent = device
    ? `${device.name}${device.channels ? ` (${device.channels} ch)` : ""}`
    : "OS default";
}

async function refreshAudioRoute() {
  const settings = collectSettings();
  if (!settings.atlasEndpoint) return;
  setText("audioRouteStatus", "Discovering audio primitives from Atlas...");
  const route = await fetch("/api/audio-route/providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings }),
  }).then((response) => response.json()).catch((error) => ({ error: String(error) }));
  if (route.error) {
    setText("audioRouteStatus", `Audio route unavailable: ${route.error}`);
    return;
  }
  state.audio.route = { ...state.audio.route, ...route };
  renderAudioRouteProviders(route);
  await Promise.all([loadAudioRouteDevices("mic"), loadAudioRouteDevices("speaker")]);
  state.settings = collectSettings();
  saveSettings();
  setText("audioRouteStatus", "Route loaded. Apply to select devices in their providers.");
}

async function loadAudioRouteDevices(side) {
  const provider = maybe(side === "mic" ? "micNodeId" : "speakerNodeId")?.value || "";
  const select = maybe(side === "mic" ? "micDeviceId" : "speakerDeviceId");
  if (!provider) {
    if (select) {
      clear(select);
      routeOption(select, "", "OS default");
    }
    return;
  }
  const isReverseBridge = (state.audio.route.bridgeProviders || [])
    .some((candidate) => candidate.id === provider);
  if (isReverseBridge) await configureReverseAudio(provider);
  const result = await fetch("/api/audio-route/devices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: collectSettings(), providerId: provider }),
  }).then((response) => response.json()).catch((error) => ({ error: String(error) }));
  if (result.error) {
    if (select) {
      clear(select);
      routeOption(select, "", `Unavailable: ${result.error}`);
      select.disabled = true;
    }
    setText("audioRouteStatus", `${provider}: ${result.error}`);
    return;
  }
  if (select) select.disabled = false;
  if (!(result.devices || []).length) {
    if (select) {
      clear(select);
      routeOption(select, "", "No devices reported by provider");
      select.disabled = true;
    }
    setText("audioRouteStatus", `${provider}: provider reported no devices`);
    return;
  }
  if (side === "mic") state.audio.route.micDevices = result.devices || [];
  else state.audio.route.speakerDevices = result.devices || [];
  renderAudioRouteDevices(side, result);
}

async function applyAudioRoute() {
  state.settings = collectSettings();
  await persistSettings().catch((error) => {
    setText("audioRouteStatus", `Settings save failed: ${error}`);
  });
  setText("audioRouteStatus", "Applying selected devices...");
  const result = await fetch("/api/audio-route/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: state.settings }),
  }).then((response) => response.json()).catch((error) => ({ error: String(error) }));
  if (!result.ok) {
    setText("audioRouteStatus", `路由应用失败: ${result.error || "未知错误"}`);
    return;
  }
  const count = Array.isArray(result.selected) ? result.selected.length : 0;
  setText("audioRouteStatus", `已对 ${count} 个选中设备应用路由。`);
  addTimeline("audio", "音频路由已应用");
}

async function startAudioServer() {
  appendAudioLog("starting client audio device server");
  const result = await fetch("/api/audio-server/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  }).then((r) => r.json());
  renderAudioServer(result);
  await checkAudioServer();
  startAudioServerStreams();
  loadAudioDevices();
}

async function checkAudioServer() {
  const status = await fetch("/api/audio-server/status").then((r) => r.json()).catch((error) => ({ error: String(error) }));
  renderAudioServer(status);
  if (!status.wsUrl) return;
  const target = new URL(status.wsUrl);
  const result = await fetch(`/api/audio-server/health?host=${encodeURIComponent(target.hostname)}&port=${encodeURIComponent(target.port)}`)
    .then((r) => r.json())
    .catch((error) => ({ error: String(error) }));
  renderAudioServer({ ...status, ...result, wsUrl: status.wsUrl, uiUrl: status.uiUrl, logPath: status.logPath });
  if (result.reachable || result.ok) {
    startAudioServerStreams();
    loadAudioDevices();
  }
}

function audioServerOnce(path, body = null) {
  return new Promise((resolve) => {
    const url = audioServerWsUrl(path);
    if (!url) {
      resolve({ ok: false, error: "client audio device server is not discovered; start or check it first" });
      return;
    }
    const socket = new WebSocket(url);
    let settled = false;
    const done = (payload) => {
      if (settled) return;
      settled = true;
      try {
        socket.close();
      } catch (_) {
        // no-op
      }
      resolve(payload);
    };
    socket.onopen = () => {
      if (body !== null) socket.send(JSON.stringify(body));
    };
    socket.onmessage = (event) => {
      try {
        done(JSON.parse(event.data));
      } catch (_) {
        done({ ok: false, error: String(event.data || "invalid bridge response") });
      }
    };
    socket.onerror = () => done({ ok: false, error: `cannot connect ${url}` });
    socket.onclose = () => done({ ok: false, error: `closed ${url}` });
  });
}

async function loadAudioDevices() {
  const result = await audioServerOnce("/devices");
  if (!result || result.ok === false) {
    appendAudioLog(`device refresh failed: ${result?.error || "unknown error"}`);
    return;
  }
  state.audio.devices = Array.isArray(result.devices) ? result.devices : [];
  state.audio.inputCurrent = result.input_current ?? result.input_default ?? null;
  state.audio.outputCurrent = result.output_current ?? result.output_default ?? null;
  renderAudioDevices(result);
  appendAudioLog(`loaded ${state.audio.devices.length} audio devices`);
}

function renderAudioDevices(result = {}) {
  const input = maybe("audioInputDevice");
  const output = maybe("audioOutputDevice");
  if (!input || !output) return;
  clear(input);
  clear(output);
  const inputCurrent = result.input_current ?? result.input_default ?? state.audio.inputCurrent;
  const outputCurrent = result.output_current ?? result.output_default ?? state.audio.outputCurrent;
  const makeOption = (device, kind) => {
    const opt = document.createElement("option");
    opt.value = String(device.id);
    const channels = kind === "input" ? device.max_input_channels : device.max_output_channels;
    opt.textContent = `#${device.id} ${device.name} (${channels} ch)`;
    return opt;
  };
  state.audio.devices
    .filter((device) => Number(device.max_input_channels || 0) > 0)
    .forEach((device) => input.appendChild(makeOption(device, "input")));
  state.audio.devices
    .filter((device) => Number(device.max_output_channels || 0) > 0)
    .forEach((device) => output.appendChild(makeOption(device, "output")));
  input.value = inputCurrent !== null && inputCurrent !== undefined ? String(inputCurrent) : "";
  output.value = outputCurrent !== null && outputCurrent !== undefined ? String(outputCurrent) : "";
}

async function applyAudioDevices() {
  const input = maybe("audioInputDevice")?.value;
  const output = maybe("audioOutputDevice")?.value;
  const body = {};
  if (input !== undefined && input !== "") body.input = Number(input);
  if (output !== undefined && output !== "") body.output = Number(output);
  appendAudioLog(`applying devices ${JSON.stringify(body)}`);
  const result = await audioServerOnce("/set_device", body);
  appendAudioLog(result.ok ? "设备选择已应用" : `设备选择失败: ${result.error || "未知错误"}`);
  await loadAudioDevices();
}

function startAudioServerStreams() {
  if (!state.audio.wsUrl) return;
  startAudioVuStream();
  startAudioLogStream();
}

function startAudioVuStream() {
  if (state.audio.vuSocket && state.audio.vuSocket.readyState <= WebSocket.OPEN) return;
  const url = audioServerWsUrl("/vu");
  if (!url) return;
  const socket = new WebSocket(url);
  state.audio.vuSocket = socket;
  socket.onopen = () => {
    setText("audioLevelState", "live");
    appendAudioLog("VU connected");
  };
  socket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      renderAudioLevel(
        Number(payload.input_level ?? payload.level ?? 0),
        Number(payload.output_level ?? 0),
      );
    } catch (_) {
      renderAudioLevel(0, 0);
    }
  };
  socket.onerror = () => setText("audioLevelState", "离线");
  socket.onclose = () => {
    setText("audioLevelState", "离线");
    state.audio.vuSocket = null;
  };
}

function startAudioLogStream() {
  if (state.audio.logSocket && state.audio.logSocket.readyState <= WebSocket.OPEN) return;
  const url = audioServerWsUrl("/log");
  if (!url) return;
  const socket = new WebSocket(url);
  state.audio.logSocket = socket;
  socket.onopen = () => appendAudioLog("log stream connected");
  socket.onmessage = (event) => appendAudioLog(event.data);
  socket.onerror = () => appendAudioLog("log stream error");
  socket.onclose = () => {
    state.audio.logSocket = null;
  };
}

function renderAudioLevel(level, outputLevel = 0) {
  const raw = Math.max(0, Math.min(1, Number.isFinite(level) ? level : 0));
  const display = Math.max(0, Math.min(1, Math.sqrt(raw) * 2.8));
  const outputRaw = Math.max(0, Math.min(1, Number.isFinite(outputLevel) ? outputLevel : 0));
  state.audio.outputLevelTarget = Math.max(0, Math.min(1, Math.pow(outputRaw, 0.4) * 1.5));
  if (state.ttsPlaying || state.audio.outputLevelTarget > 0.002 || state.audio.auraLevel > 0.002) {
    document.body.classList.add("tts-speaking");
    startTtsAuraAnimation();
  }
  state.audio.levelHistory.push(display);
  state.audio.levelHistory = state.audio.levelHistory.slice(-28);
  if (maybe("audioLevelBar")) $("audioLevelBar").style.width = `${Math.round(display * 100)}%`;
  const label = `${Math.round(display * 100)}%`;
  setText("audioLevelText", label);
  if (maybe("audioLevelText")) $("audioLevelText").title = `raw RMS ${raw.toFixed(4)}`;
  renderAudioBars();
}

function startTtsAuraAnimation() {
  if (state.audio.auraFrame) return;
  state.audio.auraFrame = requestAnimationFrame(updateTtsAuraFrame);
}

function updateTtsAuraFrame() {
  state.audio.auraFrame = 0;
  const outputActive = state.audio.outputLevelTarget > 0.002;
  const target = outputActive
    ? state.audio.outputLevelTarget
    : (state.ttsPlaying ? 0.10 : 0);
  const response = target > state.audio.auraLevel ? 0.32 : 0.14;
  state.audio.auraLevel += (target - state.audio.auraLevel) * response;
  if (Math.abs(target - state.audio.auraLevel) < 0.002) {
    state.audio.auraLevel = target;
  }
  const opacity = state.audio.auraLevel > 0
    ? Math.min(1, 0.34 + state.audio.auraLevel * 0.66)
    : 0;
  document.documentElement.style.setProperty("--voice-level", state.audio.auraLevel.toFixed(4));
  document.documentElement.style.setProperty("--voice-opacity", opacity.toFixed(4));
  if (state.ttsPlaying || outputActive || state.audio.auraLevel > 0.002) {
    state.audio.auraFrame = requestAnimationFrame(updateTtsAuraFrame);
  } else {
    document.body.classList.remove("tts-speaking");
  }
}

function setTtsAura(active) {
  state.ttsPlaying = Boolean(active);
  if (state.ttsPlaying || state.audio.outputLevelTarget > 0.002) {
    document.body.classList.add("tts-speaking");
  }
  startTtsAuraAnimation();
  syncVoiceControls();
}

function renderAudioBars() {
  const root = maybe("audioBars");
  if (!root) return;
  clear(root);
  state.audio.levelHistory.forEach((level) => {
    const bar = document.createElement("span");
    bar.style.height = `${Math.max(8, Math.round(level * 100))}%`;
    root.appendChild(bar);
  });
}

function appendAudioLog(line) {
  const root = maybe("audioLog");
  if (!root) return;
  const text = normalizeAudioLogLine(line);
  if (!text) return;
  const stamp = new Date().toLocaleTimeString();
  const lines = state.audio.logLines || [];
  const last = lines[lines.length - 1];
  if (last && last.text === text) {
    last.count = (last.count || 1) + 1;
    last.stamp = stamp;
  } else {
    lines.push({ stamp, text, count: 1 });
  }
  state.audio.logLines = lines.slice(-AUDIO_LOG_MAX_LINES);
  root.textContent = `${state.audio.logLines.map((item) => {
    const suffix = item.count > 1 ? ` x${item.count}` : "";
    return `[${item.stamp}] ${item.text}${suffix}`;
  }).join("\n")}\n`;
  root.scrollTop = root.scrollHeight;
  setText("audioLogSummary", "Audio device log.");
}

function normalizeAudioLogLine(line) {
  const text = String(line ?? "")
    .replace(/\r/g, "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean)
    .join(" ");
  if (!text) return "";
  if (/^connection open$/i.test(text)) return "";
  if (/^[<>]\s+(TEXT|BINARY|PING|PONG|CLOSE)\b/.test(text)) return "";
  if (/^[=%]\s+/.test(text) && /(connection|keepalive|opcode|frame|close|open)/i.test(text)) return "";
  if (/websockets\.(client|server|protocol|connection)/i.test(text)) return "";
  if (/opening handshake failed/i.test(text)) return "";
  if (/(^|\s)[<>]\s+TEXT\b/.test(text)) return "";
  if (text.length <= AUDIO_LOG_MAX_CHARS) return text;
  return `${text.slice(0, AUDIO_LOG_MAX_CHARS)} ... [${text.length} chars]`;
}

async function enrollVoice() {
  const userId = $("enrollUserId").value.trim() || $("userId").value.trim();
  const userName = $("enrollUserName").value.trim() || userId;
  const seconds = Number($("recordSeconds").value || 6);
  if (!userId) {
    renderEnroll({ ok: false, error: "请填写语音 ID" });
    return;
  }
  $("enrollState").textContent = `录音中 ${seconds}s`;
  $("enrollVoice").classList.add("busy");
  addTimeline("voiceprint", `为 ${userId} 录音 ${seconds}s`);
  const result = await fetch("/api/voiceprint/enroll", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      settings: collectSettings(),
      userId,
      userName,
      seconds,
    }),
  }).then((r) => r.json()).catch((error) => ({ ok: false, error: String(error) }));
  $("enrollVoice").classList.remove("busy");
  renderEnroll(result);
}

async function testSpeaker() {
  $("testSpeaker").classList.add("busy");
  addTimeline("audio", "已请求扬声器测试");
  const result = await fetch("/api/audio/play-test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      settings: collectSettings(),
      text: "Robonix speaker test. 如果你听到这句话，语音播放链路正常。",
    }),
  }).then((r) => r.json()).catch((error) => ({ ok: false, error: String(error) }));
  $("testSpeaker").classList.remove("busy");
  const text = result.ok
    ? `speaker ok: played ${result.bytes} bytes via ${result.speakerEndpoint}`
    : `speaker failed: ${result.error}`;
  const status = $("audioTestStatus");
  status.textContent = text;
  status.classList.toggle("is-error", !result.ok);
  status.classList.toggle("is-success", Boolean(result.ok));
  addMessage(result.ok ? "status" : "error", text);
  addTimeline(result.ok ? "audio" : "error", text);
  renderAudioServer({
    ok: result.ok,
    error: result.error || "",
    url: result.ok ? `tts ${result.ttsEndpoint} / speaker ${result.speakerEndpoint}` : "",
  });
}

async function testMicrophone() {
  const button = $("testMicrophone");
  button.classList.add("busy");
  addTimeline("audio", "已请求麦克风测试");
  const result = await fetch("/api/audio/mic-test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ settings: collectSettings(), seconds: 1.0 }),
  }).then((r) => r.json()).catch((error) => ({ ok: false, error: String(error) }));
  button.classList.remove("busy");
  const text = result.ok
    ? `microphone ok: ${result.bytes} bytes in ${result.captureMs} ms, RMS ${result.rms}`
    : `microphone failed: ${result.error}`;
  const status = $("audioTestStatus");
  status.textContent = text;
  status.classList.toggle("is-error", !result.ok);
  status.classList.toggle("is-success", Boolean(result.ok));
  addMessage(result.ok ? "status" : "error", text);
  addTimeline(result.ok ? "audio" : "error", text);
  setText("audioRouteStatus", text);
}

function renderEnroll(result) {
  $("enrollState").textContent = result.ok ? "已注册" : "失败";
  if (result.ok && result.userId) {
    applyVoiceUser(result.userId);
  }
  const text = result.ok
    ? `${result.alreadyEnrolled ? "已使用现有" : "已注册"} voice:${result.userId} (${result.bytes} 字节)`
    : `注册失败: ${result.error}`;
  addTimeline("voiceprint", text);
  const root = $("audioServerStatus");
  clear(root);
  const div = document.createElement("div");
  div.className = result.ok ? "ok" : "bad";
  div.textContent = text;
  root.appendChild(div);
  if (result.ok && result.message) {
    const note = document.createElement("div");
    note.className = "small";
    note.textContent = result.message;
    root.appendChild(note);
  }
}

function applyVoiceUser(rawUserId) {
  const id = normalizeVoiceId(rawUserId);
  if (!id) return;
  $("userId").value = `voice:${id}`;
  state.settings.userId = `voice:${id}`;
  saveSettings();
}

function normalizeVoiceId(rawUserId) {
  const value = String(rawUserId || "").trim();
  if (!value) return "";
  if (value.startsWith("voice:")) return value.slice("voice:".length).trim();
  if (value.startsWith("local:")) return value.slice("local:".length).trim();
  return value;
}

function renderAudioServer(result) {
  const root = maybe("audioServerStatus");
  if (!root) return;
  clear(root);
  if (result.wsUrl) state.audio.wsUrl = result.wsUrl;
  const online = Boolean(result.ok || result.reachable);
  setText("audioServerState", online ? "在线" : "离线");
  setText("audioServerSummary", online ? (result.url || result.wsUrl || "音频设备服务可访问。") : (result.error || "客户端音频设备服务离线。"));
  const lines = [
    online ? "ok" : "不可访问",
    result.error || "",
    result.wsUrl || "",
    result.uiUrl || result.url || "",
    result.logPath || "",
  ].filter(Boolean);
  lines.forEach((line) => {
    const div = document.createElement("div");
    div.className = online ? "ok" : "warn";
    div.textContent = line;
    root.appendChild(div);
  });
  appendAudioLog(lines.join(" | "));
}

function setText(id, text) {
  const node = maybe(id);
  if (node) node.textContent = text;
}

/// Retarget a composer button's caption without discarding its icon span.
function setButtonLabel(node, text) {
  if (!node) return;
  const label = node.querySelector(".btn-label");
  if (label) label.textContent = text;
  else node.textContent = text;
}

function setBusy(value) {
  state.busy = value;
  $("sendButton").classList.toggle("busy", value);
  setButtonLabel($("sendButton"), "发送");
  $("sendButton").title = value ? "向运行中的任务追加 (Enter)" : "发送任务 (Enter)";
  $("stopButton").hidden = !value;
  // Left enabled while busy on purpose: a disabled button swallows the click
  // and the "abort the running task first" guard never gets to explain
  // itself, which reads as the control being broken.
  if (maybe("newSessionAction")) $("newSessionAction").disabled = false;
  if (!value) resetStopState();
  maybe("voiceButton")?.classList.toggle("busy", value);
  document.querySelectorAll("[data-page-action='voice-start']").forEach((button) => {
    button.classList.toggle("busy", value);
    // Same button either way: it starts a recording. Whether that recording
    // opens a new task or adds to the running one is context, not a separate
    // control, so the label stays put and only the tooltip explains it.
    setButtonLabel(button, "开始录音");
  });
  // syncVoiceControls owns the tooltip and the hidden state for this button.
  syncVoiceControls();
}

function beginStream(socket = null) {
  if (socket) state.interactionSockets.add(socket);
  state.activeStreams += 1;
  setBusy(true);
}

function endStream(socket = null) {
  if (socket) state.interactionSockets.delete(socket);
  state.activeStreams = Math.max(0, state.activeStreams - 1);
  setBusy(state.activeStreams > 0 || state.taskRunning);
}

function setTextAll(selector, text) {
  document.querySelectorAll(selector).forEach((node) => {
    node.textContent = text;
  });
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// mapsApi: thin wrapper that POSTs JSON to the maps API and rejects on
// non-2xx so the UI can show a single error string. Used by every maps
// mutation in this page.
async function mapsApi(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    throw new Error(`non-JSON response from ${path} (status ${response.status})`);
  }
  if (!response.ok || (payload && payload.ok === false)) {
    const message =
      (payload && (payload.error || payload.message)) ||
      `${path} failed with status ${response.status}`;
    const result = new Error(message);
    result.payload = payload;
    throw result;
  }
  return payload;
}

// ── Maps page ──────────────────────────────────────────────────────────────
// The Maps page is split into two columns:
//   * Left  — one card per registered agent, each showing the maps that
//             live on that agent. A "同步" button on the card pulls
//             new maps from the agent into the shared library; the X
//             on each tile removes the map from the agent itself.
//   * Right — a flat grid of every map that lives in the local shared
//             library. Tiles can be dragged onto a robot card on the
//             left to deploy the map to that robot; the X removes the
//             map from the shared library entirely.
//
// Internally a "map" is one of the entries returned by the robot's
// `robonix/primitive/map/list` snapshot (file or dir). Maps are matched
// across the two columns by `<robotId>/<name>` so a map synced from
// agent A is not mistakenly thought to live on agent B.

// Per-agent map listings keyed by agentId. Each value is the latest
// `list_maps` response (or `null` while the request is in flight).
const mapsPageState = {
  byAgent: {},
  shared: null,
  sharedRoot: "",
  sharedByKey: {},
  totalShared: 0,
};

let mapsBoardInFlight = false;
const perAgentRefreshInFlight = new Set();

function setRobotsError(message) {
  const box = maybe("robotsError");
  if (!box) return;
  if (!message) {
    box.hidden = true;
    box.textContent = "";
    return;
  }
  box.hidden = false;
  box.textContent = message;
}

function setSharedError(message) {
  const box = maybe("sharedError");
  if (!box) return;
  if (!message) {
    box.hidden = true;
    box.textContent = "";
    return;
  }
  box.hidden = false;
  box.textContent = message;
}

function buildAtlasEndpointForAgent(entry) {
  if (!entry) return "";
  return buildAtlasEndpoint(entry.host, entry.atlasPort);
}

function collectSettingsForAgent(entry) {
  const settings = typeof collectSettings === "function" ? collectSettings() : {};
  const atlas = buildAtlasEndpointForAgent(entry);
  return {
    ...settings,
    robotHost: entry.host || settings.robotHost || "",
    atlasPort: entry.atlasPort || settings.atlasPort,
    atlasEndpoint: atlas || settings.atlasEndpoint || "",
  };
}

async function refreshAllMaps() {
  if (mapsBoardInFlight) return;
  mapsBoardInFlight = true;
  try {
    await Promise.all([refreshSharedBoard(), refreshRobotsBoard()]);
  } finally {
    mapsBoardInFlight = false;
  }
}

async function refreshRobotsBoard() {
  const board = maybe("robotsBoard");
  if (!board) return;
  const agents = listAgents();
  if (agents.length === 0) {
    clear(board);
    const empty = document.createElement("div");
    empty.className = "maps-robot-card-empty";
    empty.textContent = "请先在左侧添加至少一个智能体。";
    board.appendChild(empty);
    return;
  }
  // Fetch each agent's listing in parallel; render progressively so a
  // slow agent does not block the others.
  await Promise.all(agents.map((entry) => refreshAgentMaps(entry)));
  clear(board);
  for (const entry of agents) board.appendChild(buildAgentMapsCard(entry));
  const summary = maybe("robotsBoardSummary");
  if (summary) {
    const totalMaps = agents.reduce((sum, a) => sum + (mapsPageState.byAgent[a.agentId]?.files?.length || 0), 0);
    summary.textContent = `共 ${agents.length} 个智能体，${totalMaps} 张地图。`;
  }
}

async function refreshAgentMaps(entry) {
  if (!entry) return;
  if (perAgentRefreshInFlight.has(entry.agentId)) return;
  const atlas = buildAtlasEndpointForAgent(entry);
  if (!atlas) {
    mapsPageState.byAgent[entry.agentId] = { available: false, files: [], mapsDir: "", error: "未配置主机" };
    return;
  }
  perAgentRefreshInFlight.add(entry.agentId);
  try {
    const response = await fetch("/api/maps/list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: collectSettingsForAgent(entry) }),
    });
    const result = await response.json().catch(() => ({
      available: false,
      mapsDir: "",
      count: 0,
      files: [],
      error: `non-JSON response (status ${response.status})`,
    }));
    mapsPageState.byAgent[entry.agentId] = result;
  } catch (error) {
    mapsPageState.byAgent[entry.agentId] = {
      available: false,
      mapsDir: "",
      count: 0,
      files: [],
      error: String(error),
    };
  } finally {
    perAgentRefreshInFlight.delete(entry.agentId);
  }
}

function buildAgentMapsCard(entry) {
  const card = document.createElement("section");
  card.className = "maps-robot-card";
  card.dataset.agentId = entry.agentId;
  // 拖入事件
  card.addEventListener("dragover", onAgentCardDragOver);
  card.addEventListener("dragenter", onAgentCardDragEnter);
  card.addEventListener("dragleave", onAgentCardDragLeave);
  card.addEventListener("drop", onAgentCardDrop);

  // 头部：名字 + ID + 状态 + 操作按钮
  const header = document.createElement("div");
  header.className = "maps-robot-card-header";

  const title = document.createElement("div");
  title.className = "maps-robot-card-title";
  const label = document.createElement("span");
  label.textContent = entry.label || entry.agentId;
  title.appendChild(label);
  const idPill = document.createElement("span");
  idPill.className = "maps-robot-card-id";
  idPill.textContent = entry.agentId;
  title.appendChild(idPill);

  const actions = document.createElement("div");
  actions.className = "maps-robot-card-actions";
  const syncBtn = document.createElement("button");
  syncBtn.type = "button";
  syncBtn.className = "button maps-sync-button";
  syncBtn.textContent = "同步到共享库";
  syncBtn.title = "将该智能体上所有地图拉取到本地共享地图库";
  syncBtn.addEventListener("click", () => syncAgentMapsToShared(entry, syncBtn));
  actions.append(syncBtn);

  header.append(title, actions);
  card.appendChild(header);

  // 主机 / 状态
  const meta = document.createElement("div");
  meta.className = "maps-robot-card-host";
  const host = entry.host || "(未配置主机)";
  meta.textContent = `Atlas: ${host}:${entry.atlasPort || 50051}`;
  card.appendChild(meta);

  const data = mapsPageState.byAgent[entry.agentId];
  const stateRow = document.createElement("div");
  stateRow.className = "maps-robot-card-host";
  if (!data) {
    stateRow.textContent = "状态: 加载中…";
  } else if (data.error) {
    stateRow.textContent = `状态: ${data.error}`;
  } else if (!data.available) {
    stateRow.textContent = "状态: 不可用";
  } else {
    const files = Array.isArray(data.files) ? data.files : [];
    stateRow.textContent = `状态: ${data.mapsDir || ""} · ${files.length} 项`;
  }
  card.appendChild(stateRow);

  // 地图瓦片区
  const tiles = document.createElement("div");
  tiles.className = "maps-robot-card-maps";
  if (data?.error) {
    const error = document.createElement("div");
    error.className = "maps-robot-card-empty";
    error.textContent = data.error;
    tiles.appendChild(error);
  } else if (data && Array.isArray(data.files) && data.files.length > 0) {
    for (const file of data.files) tiles.appendChild(buildAgentMapTile(entry, file));
  } else {
    const empty = document.createElement("div");
    empty.className = "maps-robot-card-empty";
    empty.textContent = "暂无地图。点击「同步到共享库」以下载或等待机器狗生成。";
    tiles.appendChild(empty);
  }
  card.appendChild(tiles);
  return card;
}

function buildAgentMapTile(entry, file) {
  const tile = document.createElement("div");
  tile.className = "map-tile";
  tile.draggable = true;
  tile.dataset.kind = "robot";
  tile.dataset.agentId = entry.agentId;
  tile.dataset.mapName = file.name;
  tile.dataset.mapKey = `${entry.agentId}/${file.name}`;
  tile.title = `${file.name}（${file.kind || "file"}）— 删除会从该机器人移除`;

  const name = document.createElement("span");
  name.className = "map-tile-name";
  name.textContent = file.name;
  const meta = document.createElement("span");
  meta.className = "map-tile-meta";
  const sizeText = formatBytes(file.sizeBytes);
  meta.textContent = `${file.kind === "dir" ? "目录" : "文件"}${sizeText ? " · " + sizeText : ""}`;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "map-tile-close";
  close.setAttribute("aria-label", `删除 ${entry.agentId} 上的地图 ${file.name}`);
  close.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteAgentMap(entry.agentId, file.name);
  });
  tile.append(name, meta, close);
  // 拖拽源信息（机器人侧用于显示，不直接接收）
  tile.addEventListener("dragstart", onMapTileDragStart);
  tile.addEventListener("dragend", onMapTileDragEnd);
  return tile;
}

async function refreshSharedBoard() {
  const board = maybe("sharedBoard");
  if (!board) return;
  try {
    const response = await fetch("/api/maps/shared", { method: "GET" });
    const result = await response.json().catch(() => ({
      ok: false,
      error: `non-JSON response (status ${response.status})`,
      root: "",
      robots: [],
      totalFiles: 0,
    }));
    if (!result.ok) {
      mapsPageState.shared = result;
      setSharedError(result.error || "共享地图库不可用");
    } else {
      mapsPageState.shared = result;
      setSharedError("");
    }
  } catch (error) {
    mapsPageState.shared = { ok: false, error: String(error), root: "", robots: [], totalFiles: 0 };
    setSharedError(String(error));
  }
  renderSharedBoard();
}

function renderSharedBoard() {
  const board = maybe("sharedBoard");
  if (!board) return;
  clear(board);
  const summary = maybe("sharedBoardSummary");
  if (!mapsPageState.shared || !mapsPageState.shared.ok) {
    const empty = document.createElement("div");
    empty.className = "maps-robot-card-empty";
    empty.textContent = "共享地图库不可用";
    board.appendChild(empty);
    if (summary) summary.textContent = "共享地图库不可用";
    return;
  }
  const root = mapsPageState.shared.root || "";
  mapsPageState.sharedRoot = root;
  // 合并所有机器人下的地图为一张平铺表（同名 + 同源 = 同一个 map）
  const byKey = new Map();
  for (const robot of mapsPageState.shared.robots || []) {
    for (const file of robot.files || []) {
      const key = `${robot.robotId}/${file.name}`;
      const prev = byKey.get(key);
      if (prev) {
        // 同源同名视为同一份，保留最新的 mtime
        if ((file.mtimeUnix || 0) > (prev.mtimeUnix || 0)) {
          byKey.set(key, { ...file, sourceRobotId: robot.robotId });
        }
      } else {
        byKey.set(key, { ...file, sourceRobotId: robot.robotId });
      }
    }
  }
  mapsPageState.sharedByKey = byKey;
  mapsPageState.totalShared = byKey.size;
  if (summary) summary.textContent = `共享地图库：${root} · 共 ${byKey.size} 项`;
  if (byKey.size === 0) {
    const empty = document.createElement("div");
    empty.className = "maps-robot-card-empty";
    empty.textContent = "尚无共享地图。点击左侧机器人卡片的「同步到共享库」以下载地图。";
    board.appendChild(empty);
    return;
  }
  // 按 (sourceRobotId, name) 排序
  const entries = [...byKey.values()].sort((a, b) => {
    if (a.sourceRobotId === b.sourceRobotId) return a.name.localeCompare(b.name);
    return a.sourceRobotId.localeCompare(b.sourceRobotId);
  });
  for (const file of entries) board.appendChild(buildSharedMapTile(file));
}

function buildSharedMapTile(file) {
  const tile = document.createElement("div");
  tile.className = "map-tile";
  tile.draggable = true;
  tile.dataset.kind = "shared";
  tile.dataset.mapName = file.name;
  tile.dataset.sourceRobotId = file.sourceRobotId;
  tile.dataset.mapKey = `${file.sourceRobotId}/${file.name}`;
  tile.title = `源: ${file.sourceRobotId}/${file.name}（${file.kind || "file"}）— 拖到左侧机器人以部署`;

  const name = document.createElement("span");
  name.className = "map-tile-name";
  name.textContent = file.name;
  const meta = document.createElement("span");
  meta.className = "map-tile-meta";
  const sizeText = formatBytes(file.sizeBytes);
  const srcText = file.sourceRobotId ? `@${file.sourceRobotId}` : "";
  const parts = [file.kind === "dir" ? "目录" : "文件"];
  if (sizeText) parts.push(sizeText);
  if (srcText) parts.push(srcText);
  meta.textContent = parts.join(" · ");

  const close = document.createElement("button");
  close.type = "button";
  close.className = "map-tile-close";
  close.setAttribute("aria-label", `从共享库删除 ${file.sourceRobotId}/${file.name}`);
  close.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteSharedMap(file.sourceRobotId, file.name);
  });
  tile.append(name, meta, close);
  tile.addEventListener("dragstart", onMapTileDragStart);
  tile.addEventListener("dragend", onMapTileDragEnd);
  return tile;
}

// ── Drag and drop ────────────────────────────────────────────────────────
const MAP_MIME = "application/x-robonix-map";

function onMapTileDragStart(event) {
  const tile = event.currentTarget;
  const payload = JSON.stringify({
    kind: tile.dataset.kind,
    mapName: tile.dataset.mapName,
    sourceRobotId: tile.dataset.sourceRobotId || "",
    agentId: tile.dataset.agentId || "",
    mapKey: tile.dataset.mapKey,
  });
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = tile.dataset.kind === "shared" ? "copy" : "move";
    event.dataTransfer.setData(MAP_MIME, payload);
    event.dataTransfer.setData("text/plain", payload);
  }
  tile.classList.add("is-dragging");
}

function onMapTileDragEnd(event) {
  event.currentTarget.classList.remove("is-dragging");
  document.querySelectorAll(".maps-robot-card.is-drop-target, .maps-board-body.is-drop-target")
    .forEach((el) => el.classList.remove("is-drop-target"));
}

function getDragPayload(event) {
  const dt = event.dataTransfer;
  if (!dt) return null;
  const raw = dt.getData(MAP_MIME) || dt.getData("text/plain");
  if (!raw) return null;
  try { return JSON.parse(raw); } catch (_) { return null; }
}

function onAgentCardDragOver(event) {
  const payload = getDragPayload(event);
  if (!payload || payload.kind !== "shared") return; // 机器人上自有地图不能再拖回
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  event.currentTarget.classList.add("is-drop-target");
}

function onAgentCardDragEnter(event) {
  const payload = getDragPayload(event);
  if (!payload || payload.kind !== "shared") return;
  event.preventDefault();
  event.currentTarget.classList.add("is-drop-target");
}

function onAgentCardDragLeave(event) {
  // 仅当真正离开卡片的可见区域时才清除高亮（避免子元素抖动）
  const card = event.currentTarget;
  if (!card.contains(event.relatedTarget)) {
    card.classList.remove("is-drop-target");
  }
}

async function onAgentCardDrop(event) {
  const card = event.currentTarget;
  card.classList.remove("is-drop-target");
  const payload = getDragPayload(event);
  if (!payload) return;
  event.preventDefault();
  const targetAgentId = card.dataset.agentId;
  if (!targetAgentId) return;
  if (payload.kind !== "shared") {
    setSharedError("仅支持从共享地图库拖入");
    return;
  }
  await deploySharedMapToAgent(targetAgentId, payload.sourceRobotId, payload.mapName);
}

// ── Operations ──────────────────────────────────────────────────────────
let agentSyncInFlight = new Set();

async function syncAgentMapsToShared(entry, button) {
  if (!entry) return;
  if (agentSyncInFlight.has(entry.agentId)) return;
  const atlas = buildAtlasEndpointForAgent(entry);
  if (!atlas) {
    setRobotsError(`智能体 ${entry.agentId} 未配置主机，无法同步。`);
    return;
  }
  // 共享库按物理机器人（host）维度组织，而不是 agentId。同一 host
  // 下多个 agent 共享同一份子目录，避免重复占用磁盘。
  const hostKey = (entry.host || entry.agentId || "").trim();
  agentSyncInFlight.add(entry.agentId);
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
  }
  setRobotsError("");
  try {
    const result = await mapsApi("/api/maps/shared/sync", {
      settings: collectSettingsForAgent(entry),
      robot_id: hostKey,
    });
    const pulled = result.pulledCount || 0;
    const failed = result.failedCount || 0;
    if (failed > 0) {
      setRobotsError(`同步完成：成功 ${pulled}，失败 ${failed}`);
    } else {
      setRobotsError(`已同步 ${pulled} 项到共享地图库`);
    }
  } catch (err) {
    setRobotsError(`同步失败: ${err.message || err}`);
  } finally {
    agentSyncInFlight.delete(entry.agentId);
    if (button) {
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  }
  await refreshSharedBoard();
}

let deployInFlight = new Set();

async function deploySharedMapToAgent(targetAgentId, sourceRobotId, mapName) {
  if (!targetAgentId || !sourceRobotId || !mapName) return;
  const key = `${targetAgentId}::${sourceRobotId}::${mapName}`;
  if (deployInFlight.has(key)) return;
  const target = state.agents[targetAgentId];
  if (!target) {
    setRobotsError(`未找到目标智能体 ${targetAgentId}`);
    return;
  }
  const atlas = buildAtlasEndpointForAgent(target);
  if (!atlas) {
    setRobotsError(`目标智能体 ${targetAgentId} 未配置主机，无法部署`);
    return;
  }
  deployInFlight.add(key);
  setRobotsError("");
  try {
    const result = await mapsApi("/api/maps/shared/deploy", {
      settings: collectSettingsForAgent(target),
      robot_id: sourceRobotId,
      name: mapName,
    });
    setRobotsError(`已将 ${mapName} 部署到 ${target.label || targetAgentId}`);
    // 部署成功后刷新该机器人的地图列表
    await refreshAgentMaps(target);
    await refreshRobotsBoard();
    return result;
  } catch (err) {
    setRobotsError(`部署失败: ${err.message || err}`);
  } finally {
    deployInFlight.delete(key);
  }
}

async function deleteSharedMap(sourceRobotId, name) {
  if (!sourceRobotId || !name) return;
  if (!window.confirm(`从共享地图库删除 ${sourceRobotId}/${name}？`)) return;
  try {
    await mapsApi("/api/maps/shared/delete", { robot_id: sourceRobotId, name });
    setSharedError(`已删除 ${name}`);
  } catch (err) {
    setSharedError(`删除失败: ${err.message || err}`);
    return;
  }
  await refreshSharedBoard();
}

async function deleteAgentMap(agentId, name) {
  if (!agentId || !name) return;
  if (!window.confirm(`从智能体 ${agentId} 删除地图 ${name}？`)) return;
  const target = state.agents[agentId];
  if (!target) {
    setRobotsError(`未找到智能体 ${agentId}`);
    return;
  }
  try {
    await mapsApi("/api/maps/robot/delete", {
      settings: collectSettingsForAgent(target),
      agentId,
      name,
    });
    setRobotsError(`已从 ${target.label || agentId} 删除 ${name}`);
  } catch (err) {
    setRobotsError(`删除失败: ${err.message || err}`);
    return;
  }
  await refreshAgentMaps(target);
  await refreshRobotsBoard();
}

function formatBytes(num) {
  const value = Number(num);
  if (!Number.isFinite(value) || value < 0) return "";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  for (const unit of units) {
    if (scaled < 1024 || unit === units[units.length - 1]) {
      return `${scaled.toFixed(scaled >= 10 ? 0 : 1)} ${unit}`;
    }
    scaled /= 1024;
  }
  return `${value} B`;
}

init();
