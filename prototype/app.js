/* 导盲助手 Demo — 交互逻辑 v4 */

(function () {
  'use strict';

  const WAKE_WORDS = ['小助小助', '导盲助手', '你好小助', '小助'];

  const STORAGE_KEY = 'guideAppUser';

  const state = {
    loggedIn: false,
    user: null,
    navigating: false,
    obstacleActive: false,
    standaloneObstacle: false,
    currentStep: 0,
    agentAwake: false,
    devices: {
      glasses: { connected: true, battery: 78 },
      cane: { connected: true, battery: 65 },
      dog: { connected: false, battery: 0 },
    },
  };

  const deviceDetails = {
    glasses: {
      name: '导盲眼镜 Pro', icon: '👓', sn: 'GL-2024-00821',
      connection: 'Wi-Fi 5GHz', signal: '优秀', firmware: 'v2.1.0',
      features: ['深度相机', 'Wi-Fi 实时图传', '语义分割'],
      lastSync: '刚刚',
    },
    cane: {
      name: '智能导盲杖', icon: '🦯', sn: 'CN-2024-01567',
      connection: '蓝牙 BLE', signal: '良好', firmware: 'v1.8.3',
      features: ['方向震动指引', '避障侧向震动', '电量监测'],
      lastSync: '1 分钟前',
    },
    dog: {
      name: '导盲机器狗', icon: '🐕', sn: '未发现设备',
      connection: '—', signal: '—', firmware: '—',
      features: ['自主导航', '环境感知', '语音交互'],
      lastSync: '—',
    },
  };

  const navSteps = [
    {
      instruction: '请直行 200 米，沿长安街向东',
      label: '直行 200 米', sub: '沿长安街向东', icon: '🚶',
      dir: 'forward', bearing: 90, turnType: 'straight',
      caneVib: '前方长震 ×2', caneHint: '导盲杖前方连续震动，指示直行',
      voicePrompts: [
        '导航开始。请直行二百米，沿长安街向东。',
        '前方一百米，继续直行，保持路线。',
        '前方五十米，继续直行。',
      ],
    },
    {
      instruction: '前方路口右转，进入东单北大街',
      label: '右转进入东单北大街', sub: '路口语音提醒', icon: '↱',
      dir: 'right', bearing: 180, turnType: 'right',
      caneVib: '右侧短震 ×3', caneHint: '导盲杖右侧短震，提示右转',
      voicePrompts: [
        '前方八十米，即将到达路口。',
        '前方五十米，准备右转。',
        '前方二十米，请减速。现在右转，进入东单北大街。',
      ],
    },
    {
      instruction: '已到达地铁站，请乘坐地铁 1 号线，3 站后到达西单',
      label: '乘坐地铁 1 号线', sub: '天安门东 → 西单，3 站', icon: '🚇',
      dir: 'forward', bearing: 90, turnType: 'metro',
      caneVib: '前方间歇震', caneHint: '持杖跟随人流，前方间歇震动指引',
      voicePrompts: [
        '已到达天安门东站。请进站，乘坐地铁一号线，开往古城方向。',
        '地铁运行中，三站后到达西单站，请留意报站。',
        '即将到达西单站，请准备下车。',
      ],
    },
    {
      instruction: '已出站，请直行 150 米到达目的地',
      label: '出站后直行 150 米', sub: '到达目的地西单大悦城', icon: '🚶',
      dir: 'forward', bearing: 45, turnType: 'arrive',
      caneVib: '前方长震 ×2', caneHint: '导盲杖前方震动，指引出站方向',
      voicePrompts: [
        '已从西单站出站。请直行一百五十米。',
        '前方五十米，继续直行。',
        '目的地就在前方。已到达西单大悦城，导航结束。',
      ],
    },
  ];

  const pages = ['home', 'agent', 'navigate', 'obstacle', 'devices', 'login'];
  const agentResponses = {
    '帮我把导盲眼镜连上': '好的，正在连接导盲眼镜… 已通过 Wi-Fi 连接成功，当前电量 78%。',
    '从天安门到西单怎么走': '已为您规划路线：从天安门步行至天安门东站，乘坐地铁 1 号线 3 站至西单，出站后步行 150 米到达。全程约 42 分钟。导航与避障将同时启动，是否开始？',
    '导盲杖还有多少电': '导盲杖当前电量 65%，蓝牙连接正常，方向指引和避障震动均已启用，预计还可使用约 8 小时。',
    '开启避障模式': '已为您单独开启避障模式。导盲眼镜传输图像，手机本地识别，导盲杖震动反馈障碍物方向。无需导航也可使用。',
    '绑定机器狗': '正在搜索机器狗设备… 发现一台导盲机器狗，正在配对绑定… 绑定成功！',
    '开始导航': '融合出行已启动。导航与避障同步运行，导盲杖将指引前进方向。',
    '查询设备状态': '导盲眼镜：已连接，电量 78%。导盲杖：已连接，电量 65%，方向指引已启用。机器狗：未连接。',
  };

  const obstacleScenarios = [
    { title: '正前方有台阶', desc: '建议向右偏移 30 厘米，导盲杖右侧短震（叠加方向指引）', dir: 'right', tags: ['台阶 · 正前方 0.8m', '通道 · 右前方可通行'] },
    { title: '左前方有行人', desc: '请减速，杖体左侧短震提醒，保持直行方向', dir: 'left', tags: ['行人 · 左前方 1.2m', '通道 · 正前方可通行'] },
    { title: '前方道路畅通', desc: '可正常前行，导盲杖持续指引前进方向', dir: 'none', tags: ['通道 · 正前方可通行', '花坛 · 右侧 1.5m'] },
    { title: '右前方有障碍物', desc: '建议向左绕行，杖体右侧长震，前进方向不变', dir: 'right', tags: ['障碍物 · 右前方 0.6m', '通道 · 左前方可通行'] },
  ];

  let obstacleIndex = 0;
  let navTimer = null;
  let navVoiceTimer = null;
  let obstacleTimer = null;
  let fusedVoiceTimer = null;
  let fusedNavPromptIdx = 0;
  let fusedNavTurn = true;
  let segAnim = null;
  let isListening = false;
  let speechQueue = [];
  let speechBusy = false;
  let navVoicePromptIndex = 0;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function init() {
    updateClock();
    setInterval(updateClock, 30000);
    bindNavigation();
    bindVoice();
    bindAgent();
    bindNavigationFlow();
    bindObstacle();
    bindLogin();
    bindWakeWord();
    bindDeviceDetails();
    initSegCanvas();
    updateObstacleStatusUI();

    loadSession();
    if (state.loggedIn) {
      document.body.classList.add('logged-in');
      updateUserGreeting();
      showPage('home');
      startWakeListening();
      speak(`欢迎回来，${state.user?.name || '用户'}。您可以说「小助小助」唤醒智能体。`);
    } else {
      hideWakeBar();
      showAuthTab('login');
      showPage('login');
    }
  }

  function loadSession() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        state.user = JSON.parse(raw);
        state.loggedIn = true;
      }
    } catch (_) { /* ignore */ }
  }

  function saveSession(user) {
    state.user = user;
    state.loggedIn = true;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  }

  function clearSession() {
    state.user = null;
    state.loggedIn = false;
    localStorage.removeItem(STORAGE_KEY);
  }

  function updateUserGreeting() {
    const el = $('#greeting');
    if (el && state.user?.name) {
      el.textContent = `你好，${state.user.name}`;
    }
  }

  function showAuthTab(tab) {
    const isLogin = tab === 'login';
    $('#tab-login')?.classList.toggle('active', isLogin);
    $('#tab-register')?.classList.toggle('active', !isLogin);
    $('#tab-login')?.setAttribute('aria-selected', isLogin ? 'true' : 'false');
    $('#tab-register')?.setAttribute('aria-selected', !isLogin ? 'true' : 'false');
    const loginPanel = $('#auth-panel-login');
    const regPanel = $('#auth-panel-register');
    if (loginPanel) {
      loginPanel.classList.toggle('active', isLogin);
      loginPanel.hidden = !isLogin;
    }
    if (regPanel) {
      regPanel.classList.toggle('active', !isLogin);
      regPanel.hidden = isLogin;
    }
  }

  function validatePhone(phone) {
    return /^1\d{10}$/.test(phone);
  }

  function doLogin(phone, code, isRegister, name, bindGlasses) {
    if (!validatePhone(phone)) {
      speak('请输入正确的11位手机号');
      return;
    }
    if (!code || code.length < 4) {
      speak('请输入验证码');
      return;
    }
    if (isRegister && !name?.trim()) {
      speak('请输入您的姓名');
      showAuthTab('register');
      return;
    }

    const user = {
      name: isRegister ? name.trim() : (state.user?.name || '用户'),
      phone,
      registeredAt: isRegister ? new Date().toISOString() : state.user?.registeredAt,
    };
    saveSession(user);
    document.body.classList.add('logged-in');
    updateUserGreeting();
    showWakeBar();
    startWakeListening();
    showPage('home');

    if (isRegister) {
      const bindMsg = bindGlasses ? '导盲眼镜已自动绑定。' : '';
      speak(`注册成功，${user.name}。${bindMsg}欢迎使用导盲助手。`);
    } else {
      speak(`登录成功，${user.name}。您可以说「小助小助」唤醒智能体。`);
    }
  }

  function doLogout() {
    stopNavigation();
    stopStandaloneObstacle();
    stopFusedVoiceLoop();
    if (state._wakeRec) {
      try { state._wakeRec.stop(); } catch (_) {}
      state._wakeRec = null;
    }
    clearSession();
    document.body.classList.remove('logged-in');
    hideWakeBar();
    closeDeviceSheet();
    forceHideListening();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    showAuthTab('login');
    showPage('login');
    speak('已退出登录');
  }

  function sendCode(btnId) {
    const btn = $(btnId);
    if (!btn || btn.disabled) return;
    speak('验证码已发送，请查收短信');
    let sec = 60;
    btn.disabled = true;
    btn.textContent = `${sec}s`;
    const t = setInterval(() => {
      sec--;
      btn.textContent = sec > 0 ? `${sec}s` : '获取验证码';
      if (sec <= 0) { btn.disabled = false; clearInterval(t); }
    }, 1000);
  }

  function hideWakeBar() {
    const bar = $('#wake-bar');
    if (bar) bar.hidden = true;
  }

  function showWakeBar() {
    const bar = $('#wake-bar');
    if (bar) bar.hidden = false;
  }

  function updateClock() {
    const now = new Date();
    $('#clock').textContent = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  }

  function showPage(name) {
    if (!state.loggedIn && name !== 'login') {
      showAuthTab('login');
      name = 'login';
    }
    pages.forEach((p) => {
      const el = $(`#page-${p}`);
      if (el) el.classList.toggle('active', p === name);
    });
    $$('.nav-item').forEach((btn) => {
      const isActive = btn.dataset.page === name;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-current', isActive ? 'page' : 'false');
    });
    if (state.obstacleActive && (name === 'navigate' || name === 'obstacle')) startSegAnim();
    else if (!state.obstacleActive) stopSegAnim();
  }

  function bindNavigation() {
    $$('[data-nav]').forEach((btn) => {
      btn.addEventListener('click', () => showPage(btn.dataset.nav));
    });
    $$('[data-back]').forEach((btn) => btn.addEventListener('click', () => showPage('home')));
    $$('.nav-item').forEach((btn) => {
      btn.addEventListener('click', () => showPage(btn.dataset.page));
    });
    $$('.quick-actions .action-card').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.nav) showPage(btn.dataset.nav);
      });
    });
    $('[data-action="stop-obstacle"]')?.addEventListener('click', stopStandaloneObstacle);
  }

  function playVoiceNow(text, type) {
    updateVoiceUI(text, type);

    if (!('speechSynthesis' in window)) return;

    window.speechSynthesis.cancel();
    clearTimeout(playVoiceNow._timer);

    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'zh-CN';
    u.rate = 0.9;
    window.speechSynthesis.speak(u);

    const ms = Math.min(12000, Math.max(2500, text.length * 200));
    playVoiceNow._timer = setTimeout(() => {}, ms);
  }

  function getCurrentNavVoiceText() {
    const step = navSteps[state.currentStep];
    if (!step) return '继续沿路线前行';
    const prompts = step.voicePrompts || [step.instruction];
    return prompts[fusedNavPromptIdx % prompts.length];
  }

  function getCurrentObstacleVoiceText() {
    const s = obstacleScenarios[obstacleIndex % obstacleScenarios.length];
    if (s.dir === 'none') return `避障：${s.title}，可正常前行`;
    return `避障：${s.title}。${s.desc}`;
  }

  function startFusedVoiceLoop() {
    stopFusedVoiceLoop();
    fusedNavPromptIdx = 0;
    fusedNavTurn = true;
    $('#dual-voice-panel').hidden = false;

    const tick = () => {
      if (!state.navigating || !state.obstacleActive) return;

      if (fusedNavTurn) {
        const navText = getCurrentNavVoiceText();
        playVoiceNow(navText, 'nav');
        fusedNavPromptIdx++;
      } else {
        const obsText = getCurrentObstacleVoiceText();
        playVoiceNow(obsText, 'obstacle');
        cycleObstacleUI();
      }
      fusedNavTurn = !fusedNavTurn;
    };

    playVoiceNow('融合出行开始。导航与避障语音将交替播报。', 'nav');
    setTimeout(tick, 1200);
    fusedVoiceTimer = setInterval(tick, 4500);
  }

  function stopFusedVoiceLoop() {
    clearInterval(fusedVoiceTimer);
    fusedVoiceTimer = null;
    clearTimeout(playVoiceNow._timer);
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  }

  function cycleObstacleUI() {
    obstacleIndex = (obstacleIndex + 1) % obstacleScenarios.length;
    const s = obstacleScenarios[obstacleIndex];
    const dirs = { left: '左侧 · 短震×3', right: '右侧 · 短震×3', none: '无避障震' };
    const tagHtml = s.tags.map((t) => {
      const cls = t.includes('通道') ? 'safe' : t.includes('行人') ? 'danger' : 'warning';
      return `<span class="tag ${cls}">${t}</span>`;
    }).join('');

    ['#avoidance-title', '#avoidance-title-standalone'].forEach((sel) => {
      const el = $(sel); if (el) el.textContent = s.title;
    });
    ['#avoidance-desc', '#avoidance-desc-standalone'].forEach((sel) => {
      const el = $(sel); if (el) el.textContent = s.desc;
    });
    ['#vib-dir', '#vib-dir-standalone'].forEach((sel) => {
      const el = $(sel); if (el) el.textContent = dirs[s.dir];
    });
    ['#detection-tags', '#detection-tags-standalone'].forEach((sel) => {
      const el = $(sel); if (el) el.innerHTML = tagHtml;
    });
    const summaryText = $('#obstacle-summary-text');
    if (summaryText) summaryText.textContent = `${s.title} · ${dirs[s.dir]}`;
  }

  function speak(text, opts = {}) {
    const type = opts.type ?? 'general';
    const priority = opts.priority ?? 1;
    speechQueue.push({ text, type, priority, id: Date.now() + Math.random() });
    if (state.navigating && state.obstacleActive) {
      reorganizeFusedQueue();
    } else {
      speechQueue.sort((a, b) => b.priority - a.priority || a.id - b.id);
    }
    processSpeechQueue();
  }

  function reorganizeFusedQueue() {
    const nav = speechQueue.filter((q) => q.type === 'nav');
    const obs = speechQueue.filter((q) => q.type === 'obstacle');
    const rest = speechQueue.filter((q) => q.type !== 'nav' && q.type !== 'obstacle');
    const merged = [];
    const len = Math.max(nav.length, obs.length);
    for (let i = 0; i < len; i++) {
      if (nav[i]) merged.push(nav[i]);
      if (obs[i]) merged.push(obs[i]);
    }
    speechQueue = [...rest, ...merged];
  }

  function updateVoiceUI(text, type) {
    const dual = $('#dual-voice-panel');
    const toast = $('#voice-toast');
    const toastText = $('#toast-text');

    if (state.navigating && state.obstacleActive && dual) {
      dual.hidden = false;
    }

    if (type === 'nav') {
      const el = $('#voice-nav-current');
      const hint = $('#nav-voice-hint');
      if (el) el.textContent = text;
      if (hint) { hint.hidden = false; hint.textContent = `🧭 ${text}`; }
    }
    if (type === 'obstacle') {
      const el = $('#voice-obs-current');
      const hint = $('#obs-voice-hint');
      if (el) el.textContent = text;
      if (hint) { hint.hidden = false; hint.textContent = `🛡 ${text}`; }
    }

    if (toast && toastText) {
      const prefix = type === 'nav' ? '🧭' : type === 'obstacle' ? '🛡' : '🔊';
      toastText.textContent = `${prefix} ${text}`;
      toast.hidden = false;
      clearTimeout(speak._timer);
      speak._timer = setTimeout(() => { toast.hidden = true; }, 6000);
    }
  }

  function processSpeechQueue() {
    if (speechBusy || !speechQueue.length) return;
    speechBusy = true;
    const { text, type } = speechQueue.shift();
    updateVoiceUI(text, type);

    if (!('speechSynthesis' in window)) {
      speechBusy = false;
      setTimeout(processSpeechQueue, 800);
      return;
    }

    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'zh-CN';
    u.rate = type === 'obstacle' ? 0.95 : 0.92;
    u.onend = () => { speechBusy = false; setTimeout(processSpeechQueue, 200); };
    u.onerror = () => { speechBusy = false; setTimeout(processSpeechQueue, 200); };
    window.speechSynthesis.speak(u);
  }

  function speakNav(text) {
    if (state.navigating && state.obstacleActive) return;
    speak(text, { priority: 10, type: 'nav' });
  }

  function speakObstacle(text) {
    if (state.navigating && state.obstacleActive) return;
    speak(text, { priority: 3, type: 'obstacle' });
  }

  function clearSpeechNavUI() {
    clearNavVoiceTimers();
    stopFusedVoiceLoop();
    speechQueue = speechQueue.filter((item) => item.type !== 'nav' && item.type !== 'obstacle');
    $('#dual-voice-panel').hidden = true;
    $('#nav-voice-hint').hidden = true;
    $('#obs-voice-hint').hidden = true;
    $('#voice-nav-current').textContent = '—';
    $('#voice-obs-current').textContent = '—';
  }

  function clearNavVoiceTimers() {
    clearInterval(navVoiceTimer);
    navVoiceTimer = null;
    navVoicePromptIndex = 0;
  }

  function forceHideListening() {
    isListening = false;
    const overlay = $('#listening-overlay');
    if (overlay) {
      overlay.hidden = true;
      overlay.classList.remove('is-open');
      overlay.setAttribute('aria-hidden', 'true');
    }
    $('#waveform')?.classList.remove('active');
  }

  function showListening(show) {
    if (!show) { forceHideListening(); return; }
    isListening = true;
    const overlay = $('#listening-overlay');
    if (overlay) {
      overlay.hidden = false;
      overlay.classList.add('is-open');
      overlay.setAttribute('aria-hidden', 'false');
    }
    $('#waveform')?.classList.add('active');
  }

  function endListening(triggerAgent) {
    const wasListening = isListening;
    forceHideListening();
    if (!wasListening || !triggerAgent) return;
    if (!document.body.classList.contains('logged-in')) {
      speak('请说出您的手机号和需要绑定的设备。');
      setTimeout(() => {
        doLogin('13800138000', '123456', true, '语音用户', true);
      }, 2000);
      return;
    }
    const phrases = Object.keys(agentResponses);
    handleAgentInput(phrases[Math.floor(Math.random() * phrases.length)]);
  }

  function bindVoice() {
    const overlay = $('#listening-overlay');
    ['#global-voice-btn', '#agent-voice-btn', '#login-voice-btn'].forEach((sel) => {
      $(sel)?.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        isListening ? endListening(true) : showListening(true);
      });
    });
    const cancel = (e) => { e?.preventDefault(); e?.stopPropagation(); forceHideListening(); };
    $('#listening-cancel')?.addEventListener('click', cancel);
    overlay?.addEventListener('click', (e) => {
      if (!e.target.closest('#listening-cancel')) cancel(e);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && isListening) cancel();
    });
  }

  /* ===== 关键词唤醒 ===== */
  function bindWakeWord() {
    $('#wake-demo-btn')?.addEventListener('click', () => wakeAgent('小助小助'));
    $('#wake-bar')?.addEventListener('click', (e) => {
      if (!e.target.closest('#wake-demo-btn')) wakeAgent('小助小助');
    });
  }

  function startWakeListening() {
    setWakeUI('listening');
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) return;
    try {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      const rec = new SR();
      rec.lang = 'zh-CN';
      rec.continuous = true;
      rec.interimResults = false;
      rec.onresult = (e) => {
        const text = Array.from(e.results).map((r) => r[0].transcript).join('');
        if (WAKE_WORDS.some((w) => text.includes(w))) wakeAgent(text);
      };
      rec.onerror = () => setWakeUI('idle');
      rec.onend = () => { if (state.loggedIn && !state.agentAwake) rec.start(); };
      rec.start();
      state._wakeRec = rec;
    } catch (_) { /* demo 环境可能不支持 */ }
  }

  function setWakeUI(mode) {
    const bar = $('#wake-bar');
    const status = $('#wake-status');
    const hint = $('#wake-hint');
    if (!bar) return;
    bar.classList.remove('listening', 'awake');
    if (mode === 'listening') {
      bar.classList.add('listening');
      if (status) status.textContent = '待命中';
      if (hint) hint.textContent = '说「小助小助」或「导盲助手」唤醒智能体';
    } else if (mode === 'awake') {
      bar.classList.add('awake');
      if (status) status.textContent = '智能体已唤醒';
      if (hint) hint.textContent = '请说出您的指令…';
    } else {
      if (status) status.textContent = '待命中';
    }
  }

  function wakeAgent(trigger) {
    state.agentAwake = true;
    setWakeUI('awake');
    speak('我在，请说。');
    showPage('agent');
    addChatMessage(`（唤醒词：${trigger}）`, 'user');
    setTimeout(() => {
      addChatMessage('您好，我已唤醒。您可以让我帮您导航、查设备、绑定硬件，或规划路线。', 'agent');
    }, 600);
    setTimeout(() => { state.agentAwake = false; setWakeUI('listening'); }, 8000);
  }

  /* ===== 设备详情 ===== */
  function bindDeviceDetails() {
    $$('.device-card[data-device]').forEach((card) => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('[data-action]')) return;
        openDeviceSheet(card.dataset.device);
      });
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDeviceSheet(card.dataset.device); }
      });
    });
    $('#device-sheet-close')?.addEventListener('click', closeDeviceSheet);
    $('#device-sheet-backdrop')?.addEventListener('click', closeDeviceSheet);
  }

  function openDeviceSheet(id) {
    const d = deviceDetails[id];
    const dev = state.devices[id];
    if (!d) return;
    const connected = dev?.connected;
    const battery = dev?.battery || 0;

    $('#device-sheet-icon').textContent = d.icon;
    $('#device-sheet-title').textContent = d.name;
    $('#device-sheet-sn').textContent = connected ? `SN: ${d.sn}` : d.sn;

    $('#device-sheet-body').innerHTML = `
      <div class="sheet-badge-row">
        <span class="badge ${connected ? 'connected' : ''}">${connected ? '已连接' : '未连接'}</span>
        ${connected ? `<span class="sheet-battery">电量 ${battery}%</span>` : ''}
      </div>
      <div class="sheet-stats">
        <div class="stat"><span>连接方式</span><strong>${connected ? d.connection : '—'}</strong></div>
        <div class="stat"><span>信号</span><strong>${connected ? d.signal : '—'}</strong></div>
        <div class="stat"><span>固件</span><strong>${connected ? d.firmware : '—'}</strong></div>
        <div class="stat"><span>最近同步</span><strong>${connected ? d.lastSync : '—'}</strong></div>
      </div>
      <div class="sheet-features">
        <span class="cfb-label">功能特性</span>
        <div class="feature-tags">${d.features.map((f) => `<span class="feature-tag">${f}</span>`).join('')}</div>
      </div>
      ${id === 'cane' && connected ? `<div class="sheet-cane-info"><span class="cfb-label">震动能力</span><p>前进方向长震 · 转向侧震 · 避障叠加震</p></div>` : ''}
    `;

    const actions = $('#device-sheet-actions');
    if (id === 'dog' && !connected) {
      actions.innerHTML = '<button class="btn-primary full bind-dog-btn">绑定机器狗</button>';
      actions.querySelector('.bind-dog-btn')?.addEventListener('click', () => { closeDeviceSheet(); handleAgentInput('绑定机器狗'); });
    } else if (connected) {
      actions.innerHTML = `<button class="btn-outline" type="button">断开连接</button><button class="btn-outline" type="button">${id === 'cane' ? '测试震动' : '固件升级'}</button>`;
    } else {
      actions.innerHTML = '';
    }

    $('#device-sheet-backdrop').hidden = false;
    const sheet = $('#device-sheet');
    sheet.hidden = false;
    sheet.classList.add('is-open');
    speak(`${d.name}，${connected ? `已连接，电量${battery}%` : '未连接'}`);
  }

  function closeDeviceSheet() {
    $('#device-sheet-backdrop').hidden = true;
    const sheet = $('#device-sheet');
    sheet.classList.remove('is-open');
    sheet.hidden = true;
  }

  function bindAgent() {
    $$('.chip').forEach((chip) => chip.addEventListener('click', () => handleAgentInput(chip.dataset.say)));
  }

  function handleAgentInput(text) {
    addChatMessage(text, 'user');
    setTimeout(() => {
      const reply = agentResponses[text] || `好的，我理解您说的是"${text}"。正在为您处理…`;
      addChatMessage(reply, 'agent');
      speak(reply);
      if (text.includes('导航') || text.includes('怎么走')) setTimeout(() => showPage('navigate'), 1500);
      if (text.includes('避障')) {
        setTimeout(() => { showPage('obstacle'); startStandaloneObstacle(); }, 1500);
      }
      if (text.includes('绑定机器狗')) { state.devices.dog.connected = true; state.devices.dog.battery = 92; updateDeviceUI(); }
      if (text.includes('开始导航')) startNavigation();
    }, 800);
  }

  function addChatMessage(text, role) {
    const area = $('#chat-area');
    if (!area) return;
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.innerHTML = `<div class="msg-avatar" aria-hidden="true">${role === 'agent' ? '🤖' : '👤'}</div><div class="msg-bubble"><p>${text}</p></div>`;
    area.appendChild(div);
    area.scrollTop = area.scrollHeight;
  }

  function bindNavigationFlow() {
    $('#plan-route-btn')?.addEventListener('click', () => {
      const dest = $('#route-to')?.value || '西单大悦城';
      speak(`路线已规划，前往${dest}。开始后将同步启动语音导航、避障与杖体方向指引。`);
    });
    $('#start-nav-btn')?.addEventListener('click', startNavigation);
    $('[data-action="stop-nav"]')?.addEventListener('click', stopNavigation);
  }

  function startNavigation() {
    state.navigating = true;
    state.obstacleActive = true;
    state.currentStep = 0;
    fusedNavPromptIdx = 0;
    $('#nav-summary').hidden = false;
    $('#fusion-cane-status')?.classList.add('active');
    showPage('navigate');
    startSegAnim();
    cycleObstacleUI();
    updateNavStep();
    updateObstacleStatusUI();
    startFusedVoiceLoop();
    navTimer = setInterval(() => {
      state.currentStep++;
      fusedNavPromptIdx = 0;
      if (state.currentStep >= navSteps.length) {
        stopNavigation();
        playVoiceNow('您已到达目的地，西单大悦城。导航结束。', 'nav');
        return;
      }
      updateNavStep();
    }, 12000);
  }

  function stopNavigation() {
    state.navigating = false;
    clearInterval(navTimer);
    stopFusedVoiceLoop();
    clearSpeechNavUI();
    $('#nav-summary').hidden = true;
    $('#fusion-cane-status')?.classList.remove('active');
    $$('.step').forEach((s) => s.classList.remove('active', 'done'));
    if (!state.standaloneObstacle) {
      state.obstacleActive = false;
      clearInterval(obstacleTimer);
      stopSegAnim();
    }
    updateObstacleStatusUI();
  }

  function startStepVoicePrompts(step) {
    clearNavVoiceTimers();
    if (state.navigating && state.obstacleActive) return;
    if (!step?.voicePrompts?.length) return;
    navVoicePromptIndex = 0;
    speakNav(step.voicePrompts[0]);
    if (step.voicePrompts.length > 1) {
      navVoiceTimer = setInterval(() => {
        navVoicePromptIndex++;
        if (navVoicePromptIndex < step.voicePrompts.length) {
          speakNav(step.voicePrompts[navVoicePromptIndex]);
        } else {
          clearNavVoiceTimers();
        }
      }, 2800);
    }
  }

  function startStandaloneObstacle() {
    state.standaloneObstacle = true;
    state.obstacleActive = true;
    $('#obstacle-summary').hidden = false;
    showPage('obstacle');
    startSegAnim();
    startObstacleCycle();
    cycleObstacle();
    updateObstacleStatusUI();
    speak('避障模式已单独开启。导盲眼镜识别中，导盲杖震动反馈已启用。');
  }

  function stopStandaloneObstacle() {
    state.standaloneObstacle = false;
    if (!state.navigating) {
      state.obstacleActive = false;
      clearInterval(obstacleTimer);
      stopSegAnim();
    }
    $('#obstacle-summary').hidden = true;
    updateObstacleStatusUI();
    speak('避障模式已关闭。');
  }

  function updateObstacleStatusUI() {
    const status = $('#obstacle-status');
    const toggle = $('#toggle-obstacle');
    const inline = $('#inline-obs-status');
    const active = state.standaloneObstacle || state.navigating;

    if (status) {
      status.textContent = state.standaloneObstacle ? '独立运行' : state.navigating ? '融合运行' : '未启动';
      status.classList.toggle('active', !!state.standaloneObstacle);
      status.classList.toggle('fusion', state.navigating && !state.standaloneObstacle);
      status.classList.toggle('paused', !active);
    }
    if (toggle) {
      toggle.textContent = state.standaloneObstacle ? '关闭避障' : '开启避障';
      toggle.classList.toggle('btn-secondary', state.standaloneObstacle);
      toggle.classList.toggle('btn-primary', !state.standaloneObstacle);
    }
    if (inline) {
      inline.textContent = active ? '运行中' : '未启动';
      inline.classList.toggle('active', active);
    }
    const homeBadge = $('#home-obstacle-badge');
    if (homeBadge) homeBadge.hidden = !state.navigating;
    const obsSummary = $('#obstacle-summary');
    if (obsSummary) obsSummary.hidden = !state.standaloneObstacle || state.navigating;
  }

  function updateCaneDirection(step) {
    const arrows = { forward: '↑', right: '↱', left: '↰', back: '↓' };
    const arrow = $('#compass-arrow');
    if (arrow) {
      arrow.textContent = arrows[step.dir] || '↑';
      arrow.style.transform = `rotate(${step.bearing}deg)`;
    }
    $('#cane-dir-label').textContent = step.label.split(' ')[0] === '右转' ? '右转' : step.label.split(' ')[0] === '乘坐' ? '跟随指引' : '直行';
    $('#cane-vib-label').textContent = step.caneVib;
    $('#cane-hint-text').textContent = step.caneHint;
    const homeIcon = $('#home-cane-icon');
    const homeText = $('#home-cane-text');
    if (homeIcon) homeIcon.textContent = arrows[step.dir] || '↑';
    if (homeText) homeText.textContent = `${step.label.split(' ')[0]} · ${step.caneVib}`;
  }

  function updateNavStep() {
    const step = navSteps[state.currentStep];
    if (!step) return;
    const dest = $('#route-to')?.value || '西单大悦城';
    $('#nav-dest-text').textContent = `前往 ${dest}`;
    $('#nav-instruction-text').textContent = step.instruction;
    updateCaneDirection(step);
    startStepVoicePrompts(step);
    $$('.step').forEach((s, i) => {
      s.classList.toggle('active', i === state.currentStep);
      s.classList.toggle('done', i < state.currentStep);
      if (i === state.currentStep) {
        s.querySelector('.step-content strong').textContent = step.label;
        s.querySelector('.step-content span').textContent = step.sub;
        s.querySelector('.step-icon').textContent = step.icon;
      }
    });
  }

  function startObstacleCycle() {
    clearInterval(obstacleTimer);
    const interval = state.navigating ? 3500 : 5000;
    obstacleTimer = setInterval(() => {
      if (!state.obstacleActive) return;
      cycleObstacle();
    }, interval);
  }

  function bindObstacle() {
    $('#toggle-obstacle')?.addEventListener('click', () => {
      if (state.standaloneObstacle) stopStandaloneObstacle();
      else startStandaloneObstacle();
    });
  }

  function cycleObstacle() {
    if (state.navigating) {
      cycleObstacleUI();
      return;
    }
    cycleObstacleUI();
    if (state.obstacleActive) {
      const s = obstacleScenarios[obstacleIndex];
      const voiceText = s.dir === 'none'
        ? `避障：${s.title}，可正常前行`
        : `避障：${s.title}。${s.desc}`;
      playVoiceNow(voiceText, 'obstacle');
    }
  }

  function initSegCanvas() {
    ['#seg-canvas', '#seg-canvas-standalone'].forEach((sel) => {
      const canvas = $(sel);
      if (canvas) canvas._ctx = canvas.getContext('2d');
    });
  }

  function startSegAnim() {
    stopSegAnim();
    const canvases = ['#seg-canvas', '#seg-canvas-standalone'].map((sel) => $(sel)).filter(Boolean);
    if (!canvases.length) return;
    let frame = 0;

    function drawCanvas(canvas) {
      const ctx = canvas._ctx;
      if (!ctx) return;
      const w = canvas.width, h = canvas.height;
      ctx.fillStyle = '#1a1a2e';
      ctx.fillRect(0, 0, w, h);
      for (let y = 0; y < h; y += 4) {
        for (let x = 0; x < w; x += 4) {
          const n = Math.sin(x * 0.02 + frame * 0.03) * Math.cos(y * 0.02 + frame * 0.02);
          const colors = ['#2d6a4f', '#40916c', '#d4a373', '#e76f51', '#264653'];
          ctx.fillStyle = colors[Math.floor((n + 1) * 2.5) % colors.length];
          ctx.fillRect(x, y, 4, 4);
        }
      }
      ctx.fillStyle = 'rgba(231, 111, 81, 0.5)';
      ctx.beginPath();
      ctx.ellipse(w * 0.5 + Math.sin(frame * 0.05) * 20, h * 0.55, 60, 25, 0, 0, Math.PI * 2);
      ctx.fill();
    }

    function draw() {
      if (!state.obstacleActive) return;
      canvases.forEach(drawCanvas);
      frame++;
      segAnim = requestAnimationFrame(draw);
    }
    draw();
  }

  function stopSegAnim() {
    if (segAnim) cancelAnimationFrame(segAnim);
    segAnim = null;
  }

  function bindLogin() {
    $('#tab-login')?.addEventListener('click', () => showAuthTab('login'));
    $('#tab-register')?.addEventListener('click', () => showAuthTab('register'));
    $('#go-register')?.addEventListener('click', () => showAuthTab('register'));
    $('#go-login')?.addEventListener('click', () => showAuthTab('login'));

    $('#login-send-code')?.addEventListener('click', () => sendCode('#login-send-code'));
    $('#register-send-code')?.addEventListener('click', () => sendCode('#register-send-code'));

    $('#login-btn')?.addEventListener('click', () => {
      doLogin($('#login-phone')?.value?.trim(), $('#login-code')?.value?.trim(), false);
    });

    $('#register-btn')?.addEventListener('click', () => {
      doLogin(
        $('#register-phone')?.value?.trim(),
        $('#register-code')?.value?.trim(),
        true,
        $('#register-name')?.value?.trim(),
        $('#register-bind-glasses')?.checked
      );
    });

    $('#logout-btn')?.addEventListener('click', doLogout);
    $('#logout-btn-devices')?.addEventListener('click', doLogout);
  }

  function updateDeviceUI() {
    if (!state.devices.dog.connected) return;
    const card = document.querySelector('[data-device="dog"]');
    if (!card) return;
    card.classList.remove('disconnected');
    card.classList.add('connected');
    card.setAttribute('aria-label', '机器狗，已连接，电量92%，点击查看详情');
    deviceDetails.dog.sn = 'DG-2024-00312';
    const info = card.querySelector('.device-info');
    if (info) {
      info.innerHTML = `<h3>机器狗</h3><p class="conn-status connected-text">已连接 · Wi-Fi</p>
        <div class="battery-bar" role="progressbar" aria-valuenow="92"><div class="battery-fill" style="width:92%"></div></div>
        <span class="battery-text">电量 92%</span>`;
    }
  }

  $('#scan-devices')?.addEventListener('click', () => {
    const panel = $('#scan-panel');
    if (panel) {
      panel.hidden = false;
      speak('正在搜索附近设备。');
      setTimeout(() => { panel.hidden = true; speak('搜索完成，发现一台导盲机器狗。'); }, 3000);
    }
  });

  $$('.bind-dog-btn, [data-action="bind-dog"]').forEach((btn) => {
    btn.addEventListener('click', (e) => { e.stopPropagation(); handleAgentInput('绑定机器狗'); });
  });

  function boot() {
    if (boot.done) return;
    boot.done = true;
    init();
    forceHideListening();
  }

  document.addEventListener('DOMContentLoaded', boot);
  if (document.readyState !== 'loading') boot();
})();
