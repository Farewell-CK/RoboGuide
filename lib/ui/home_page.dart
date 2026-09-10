import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';

import '../audio/mic_controller.dart';
import '../audio/speaker_controller.dart';
import '../capture/capture_controller.dart';
import '../config/app_config.dart';
import '../connection/connection_manager.dart';
import '../connection/health_stats.dart';
import '../bluetooth/bluetooth_spp.dart';
import '../session/session_controller.dart';
import '../session/session_store.dart';
import '../transport/robot_transport.dart';
import '../transport/ws_transport.dart';
import 'settings_page.dart';
import 'session_list_page.dart';
import 'widgets/ptt_button.dart';
import 'widgets/status_card.dart';
import 'widgets/turn_bubble.dart';

/// Home page: wires ConnectionManager + SessionController to the UI.
class HomePage extends StatefulWidget {
  final AppSettings settings;

  /// Non-null when the mic runtime permission was denied at startup; shown
  /// as a dismissible banner (PTT would otherwise fail silently).
  final String? micBlockedNotice;
  const HomePage({super.key, required this.settings, this.micBlockedNotice});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final SessionStore _store = SessionStore();
  final BluetoothSpp _deviceProbe = BluetoothSpp();
  late final ConnectionManager _conn;
  late final MicController _mic;
  late final SpeakerController _speaker;
  late final SessionController _session;
  final HealthStats _stats = HealthStats();
  late final CaptureController _capture;

  List<Map<String, dynamic>> _devices = [];
  List<ConversationSession> _sessions = [];
  ConversationSession? _currentSession;
  List<ConversationTurn> _renderedTurns = [];
  String? _selectedMac;
  bool _connected = false;
  bool _connecting = false;
  bool _micActive = false;
  String _audioState = 'idle';
  String _linkQuality = '-';
  String _mode = 'spp';
  int _reconnects = 0;
  String _log = '';

  List<ConversationTurn> get _turns =>
      _currentSession?.turns ?? const <ConversationTurn>[];

  @override
  void initState() {
    super.initState();
    _selectedMac = widget.settings.mac;
    _mode = widget.settings.mode.name;
    _conn = ConnectionManager(widget.settings);
    _mic = MicController();
    _speaker = SpeakerController();
    _capture = CaptureController(
      baseDir: Directory.systemTemp,
      enabled: widget.settings.captureAudio,
      onError: (m) => _appendLog('捕获: $m'),
    );
    _session = SessionController(
      transport: () => _conn.transport ?? _DisconnectedTransport.instance,
      mic: _mic,
      speaker: _speaker,
      stats: _stats,
      capture: _capture,
      onCaptureSaved: (path) => _appendLog('捕获已保存: $path'),
      // WS-mode voice-session driving hooks. Resolved at call time (not
      // fixed at initState) so switching backend in settings takes effect
      // without recreating the controller.
      onNewSessionId: () =>
          _conn.transport is WsTransport ? _nextWsSessionId() : null,
      onBeginVoiceSession: (sessionId, historyJson) async {
        final t = _conn.transport;
        if (t is WsTransport) {
          await t.beginVoiceSession(sessionId, historyJson);
        }
      },
      onEndVoiceCapture: (sessionId) async {
        final t = _conn.transport;
        if (t is WsTransport) {
          await t.endVoiceCapture(sessionId);
        }
      },
      onHistoryJson: () =>
          jsonEncode({'source': 'roboguide-ws', 'history': _recentHistory()}),
    );

    _conn.status.listen(_onConnStatus);
    _conn.logs.listen(_appendLog);
    _session.turnUpdates.listen(_onTurnUpdate);
    _session.audioState.listen((s) {
      if (mounted) setState(() => _audioState = s);
    });
    // CRITICAL: attach subscribes SessionController to the transport's
    // control/audio streams (voice_event -> turn updates, PCM -> playback).
    // Without it the app never reacts to robot responses (RX stays 0,
    // turns hang in "recognizing"). detach happens inside _session.dispose().
    _session.attach();

    _loadSessions();
    _loadDevices();
    _initCaptureDir();
    // 启动只自动连蓝牙；WS 是调试通道，须手动点连接
    if (_mode != 'ws') unawaited(_connect());
  }

  Future<void> _initCaptureDir() async {
    try {
      final docs = await getApplicationDocumentsDirectory();
      _capture.baseDir = docs;
      _appendLog('捕获目录: ${_capture.baseDir.path}/captures');
    } catch (e) {
      _capture.enabled = false;
      _appendLog('获取捕获目录失败，离线捕获不可用: $e');
    }
  }

  void _onConnStatus(TransportStatus s) {
    if (!mounted) return;
    setState(() {
      switch (s.kind) {
        case TransportStatusKind.connected:
          _connected = true;
          _connecting = false;
          _reconnects = _conn.reconnectCount;
          // Transport instance is now live; (re)bind SessionController to
          // its control/audio streams (early attach in initState grabbed
          // the disconnected stub's empty streams).
          _session.attach();
        case TransportStatusKind.connecting:
          _connecting = true;
        case TransportStatusKind.disconnected:
          _connected = false;
          _connecting = false;
      }
    });
    if (s.kind == TransportStatusKind.disconnected) {
      unawaited(_session.onDisconnected());
    }
  }

  void _onTurnUpdate(ConversationTurn turn) {
    if (!mounted) return;
    final s = _ensureSession();
    if (!s.turns.contains(turn)) {
      s.turns.insert(0, turn);
    }
    s.lastActiveAt = DateTime.now();
    // 首句 ASR 设会话标题(前 12 字),与旧行为一致
    if (turn.userText.isNotEmpty && (s.title == '新会话' || s.title.isEmpty)) {
      s.title = turn.userText.length > 12
          ? turn.userText.substring(0, 12)
          : turn.userText;
    }
    setState(() {
      _micActive = _session.micActive;
      _linkQuality = _stats.linkQuality;
      _reconnects = _conn.reconnectCount;
      _renderedTurns = List.of(s.turns);
    });
    _persist();
  }

  // ── sessions ────────────────────────────────────────────────────────
  Future<void> _loadSessions() async {
    _sessions = await _store.load();
    final curId = await _store.loadCurrentId();
    if (_sessions.isNotEmpty) {
      _currentSession = _sessions.firstWhere(
        (s) => s.id == curId,
        orElse: () => _sessions.first,
      );
    }
    if (mounted) {
      setState(() => _renderedTurns = List.of(_turns));
    }
  }

  void _persist() {
    unawaited(_store.save(_sessions, _currentSession?.id));
  }

  ConversationSession _ensureSession() {
    final cur = _currentSession;
    if (cur != null) return cur;
    final s = ConversationSession(
      id: DateTime.now().microsecondsSinceEpoch.toString(),
    );
    _sessions.insert(0, s);
    _currentSession = s;
    return s;
  }

  void _newSession() {
    setState(() => _ensureSession());
    _persist();
  }

  void _selectSession(String? id) {
    if (id == null) return;
    final s = _sessions.where((x) => x.id == id).firstOrNull;
    if (s == null) return;
    setState(() {
      _currentSession = s;
      _renderedTurns = List.of(s.turns);
    });
    _persist();
  }

  Future<void> _renameSession([ConversationSession? session]) async {
    final s = session ?? _currentSession;
    if (s == null) return;
    final controller = TextEditingController(text: s.title);
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('重命名会话'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(hintText: '输入会话名称'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text),
            child: const Text('确定'),
          ),
        ],
      ),
    );
    if (name != null && name.trim().isNotEmpty) {
      setState(() => s.title = name.trim());
      _persist();
    }
  }

  void _clearSessionTurns() {
    final s = _currentSession;
    if (s == null) return;
    setState(() {
      s.turns.clear();
      _renderedTurns = [];
    });
    _persist();
  }

  Future<void> _deleteSession(ConversationSession s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('删除会话「${s.title}」？'),
        content: const Text('会话内的所有对话记录将被删除，不可恢复。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() {
      _sessions.remove(s);
      if (_currentSession == s) {
        _currentSession = _sessions.isEmpty ? null : _sessions.first;
      }
      _renderedTurns = List.of(_turns);
    });
    _persist();
  }

  void _openSessionList() {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => SessionListPage(
          sessions: _sessions,
          currentId: _currentSession?.id,
          onSelect: (s) {
            _selectSession(s.id);
            Navigator.of(context).pop();
          },
          onNewSession: () {
            _newSession();
            Navigator.of(context).pop();
          },
          onRename: _renameSession,
          onDelete: _deleteSession,
        ),
      ),
    );
  }

  // ── WS-mode voice-session helpers ────────────────────────────────────
  int _wsSessionCounter = 0;

  /// Stable per-app session id: server-side Pilot history survives across
  /// PTT turns within the same conversation.
  String _nextWsSessionId() {
    final base = _currentSession?.id;
    if (base != null && base.isNotEmpty) return 'ws-$base';
    return 'ws-${DateTime.now().millisecondsSinceEpoch}-${_wsSessionCounter++}';
  }

  /// Recent turns as {role, text} pairs (time-ascending), mirroring the SPP
  /// path's mic_end history payload.
  List<Map<String, String>> _recentHistory() {
    final s = _currentSession;
    if (s == null) return const [];
    final history = <Map<String, String>>[];
    for (final t in s.turns.reversed.take(5)) {
      if (t.userText.isNotEmpty)
        history.add({'role': 'user', 'text': t.userText});
      if (t.assistantText.isNotEmpty) {
        history.add({'role': 'assistant', 'text': t.assistantText});
      }
    }
    return history;
  }

  // ── connection ──────────────────────────────────────────────────────
  Future<void> _loadDevices() async {
    try {
      final devices = await _deviceProbe.pairedDevices();
      if (!mounted) return;
      setState(() => _devices = devices);
      final thor = devices.where(
        (d) =>
            (d['address'] as String) == 'F8:3D:C6:91:8D:69' ||
            (d['name'] as String).toLowerCase().contains('roboguide') ||
            (d['name'] as String).toLowerCase().contains('localhost'),
      );
      if (thor.isNotEmpty) {
        setState(() => _selectedMac = thor.first['address'] as String);
        widget.settings.mac = _selectedMac!;
        unawaited(widget.settings.save());
      }
    } catch (e) {
      _appendLog('load devices failed: $e');
    }
  }

  Future<void> _connect() async {
    if (_connected || _connecting) return;
    widget.settings.mode = _mode == 'ws' ? TransportMode.ws : TransportMode.spp;
    widget.settings.mac = _selectedMac ?? widget.settings.mac;
    unawaited(widget.settings.save());
    _stats.reset();
    _conn.reconnectCount = 0;
    await _conn.start();
  }

  Future<void> _disconnect() async {
    await _conn.stop();
  }

  void _openSettings() {
    final prevMode = _mode;
    final prevMac = _selectedMac;
    final prevHost = widget.settings.wsHost;
    final prevPort = widget.settings.wsPort;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => SettingsPage(
          settings: widget.settings,
          onChanged: () {
            if (!mounted) return;
            final linkChanged =
                prevMode != widget.settings.mode.name ||
                prevMac != widget.settings.mac ||
                prevHost != widget.settings.wsHost ||
                prevPort != widget.settings.wsPort;
            setState(() {
              _mode = widget.settings.mode.name;
              _selectedMac = widget.settings.mac;
              _capture.enabled = widget.settings.captureAudio;
            });
            // 仅传输后端/设备/端点变化才重启链路；捕获开关不打断当前连接
            if (_connected && linkChanged) {
              unawaited(() async {
                await _conn.stop();
                await _connect();
              }());
            }
          },
        ),
      ),
    );
  }

  // ── misc ────────────────────────────────────────────────────────────
  void _appendLog(String line) {
    if (!mounted) return;
    setState(() {
      _log = '${DateTime.now().toString().substring(11, 19)} $line\n$_log'
          .split('\n')
          .take(8)
          .join('\n');
    });
  }

  void _removeTurn(ConversationTurn turn) {
    final s = _currentSession;
    if (s == null) return;
    setState(() {
      s.turns.remove(turn);
      _renderedTurns = List.of(s.turns);
    });
    _persist();
  }

  @override
  void dispose() {
    _deviceProbe.dispose();
    _session.dispose();
    _conn.dispose();
    _mic.dispose();
    _speaker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final turns = _renderedTurns;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Roboguide Remote'),
        actions: [
          IconButton(
            onPressed: _openSessionList,
            icon: const Icon(Icons.forum_outlined),
            tooltip: '会话列表',
          ),
          IconButton(
            onPressed: _openSettings,
            icon: const Icon(Icons.settings_outlined),
            tooltip: '设置',
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (widget.micBlockedNotice != null)
              Material(
                color: Theme.of(context).colorScheme.errorContainer,
                borderRadius: BorderRadius.circular(8),
                child: InkWell(
                  borderRadius: BorderRadius.circular(8),
                  onTap: () => openAppSettings(),
                  child: Padding(
                    padding: const EdgeInsets.all(10),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            widget.micBlockedNotice!,
                            style: TextStyle(
                              fontSize: 12,
                              color: Theme.of(context)
                                  .colorScheme
                                  .onErrorContainer,
                            ),
                          ),
                        ),
                        const Icon(Icons.settings, size: 18),
                      ],
                    ),
                  ),
                ),
              ),
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    initialValue: _currentSession?.id,
                    isExpanded: true,
                    hint: const Text('选择会话'),
                    items: _sessions
                        .map(
                          (s) => DropdownMenuItem(
                            value: s.id,
                            child: Text(
                              s.title,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: _selectSession,
                    decoration: const InputDecoration(
                      labelText: '会话',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 4),
                IconButton(
                  onPressed: _newSession,
                  icon: const Icon(Icons.add_circle_outline),
                  tooltip: '新建会话',
                ),
                IconButton(
                  onPressed: _currentSession == null
                      ? null
                      : () => _renameSession(),
                  icon: const Icon(Icons.edit_outlined),
                  tooltip: '重命名会话',
                ),
                IconButton(
                  onPressed: turns.isEmpty ? null : _clearSessionTurns,
                  icon: const Icon(Icons.delete_sweep_outlined),
                  tooltip: '清空当前会话',
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                if (_mode == 'ws') ...[
                  Expanded(
                    child: InkWell(
                      onTap: _connected ? null : _openSettings,
                      child: InputDecorator(
                        decoration: const InputDecoration(
                          labelText: 'Robot (WS)',
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                        child: Text(
                          '${widget.settings.wsHost}:${widget.settings.wsPort}',
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                  ),
                ] else ...[
                  Expanded(
                    child: DropdownButtonFormField<String>(
                      initialValue: _selectedMac,
                      isExpanded: true,
                      items: _devices
                          .map(
                            (d) => DropdownMenuItem(
                              value: d['address'] as String,
                              child: Text(
                                '${d['name']} (${d['address']})',
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: _connected
                          ? null
                          : (mac) => setState(() => _selectedMac = mac),
                      decoration: const InputDecoration(
                        labelText: 'Robot (Bluetooth)',
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                    ),
                  ),
                  const SizedBox(width: 4),
                  IconButton(
                    onPressed: _connected ? null : _loadDevices,
                    icon: const Icon(Icons.refresh),
                    tooltip: '刷新设备',
                  ),
                ],
                const SizedBox(width: 4),
                FilledButton.icon(
                  onPressed: (_connected || _connecting)
                      ? _disconnect
                      : _connect,
                  icon: Icon(
                    _connected
                        ? Icons.link_off
                        : _mode == 'ws'
                        ? Icons.wifi
                        : Icons.bluetooth,
                  ),
                  label: Text(
                    _connected
                        ? '断开'
                        : _connecting
                        ? '连接中'
                        : '连接',
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            StatusCard(
              connected: _connected,
              connecting: _connecting,
              mode: _mode,
              micActive: _micActive,
              audioState: _audioState,
              sentBytes: _stats.txAudioBytes,
              receivedBytes: _stats.rxAudioBytes,
              reconnects: _reconnects,
              linkQuality: _linkQuality,
            ),
            const SizedBox(height: 10),
            PttButton(
              enabled: _connected || _capture.enabled,
              talking: _micActive,
              onPressStart: () => unawaited(_session.startTalking()),
              onPressEnd: () => unawaited(_session.stopTalking()),
            ),
            if (_capture.enabled && !_connected)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  '离线捕获中：未连接也可录音，松开即保存 .sppwire 捕获包',
                  style: TextStyle(
                    fontSize: 11,
                    color: Theme.of(context).colorScheme.outline,
                  ),
                ),
              ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: Text(
                    _currentSession == null
                        ? '尚无会话'
                        : '会话：${_currentSession!.title}（${_currentSession!.turns.length} 回合）',
                    style: Theme.of(context).textTheme.titleSmall,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            Expanded(
              child: turns.isEmpty
                  ? Center(
                      child: Text(
                        _currentSession == null
                            ? '点击 ➕ 新建会话，按住下方按钮开始说话'
                            : '暂无对话，按住下方按钮开始说话',
                      ),
                    )
                  : ListView.builder(
                      itemCount: turns.length,
                      itemBuilder: (context, index) {
                        final turn = turns[index];
                        return TurnBubble(
                          turn: turn,
                          onDelete: () => _removeTurn(turn),
                        );
                      },
                    ),
            ),
            if (_log.isNotEmpty)
              SizedBox(
                height: 58,
                child: Text(
                  _log,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Stand-in used before the transport exists; all calls no-op.
class _DisconnectedTransport implements RobotTransport {
  static final _DisconnectedTransport instance = _DisconnectedTransport._();
  _DisconnectedTransport._();

  @override
  Stream<Uint8List> get audio => const Stream.empty();
  @override
  Stream<Map<String, dynamic>> get control => const Stream.empty();
  @override
  bool get connected => false;
  @override
  Future<void> connect() async {}
  @override
  Future<void> disconnect() async {}
  @override
  void dispose() {}
  @override
  TransportMode get mode => TransportMode.spp;
  @override
  Future<void> sendAudio(Uint8List pcm) async {}
  @override
  Future<void> sendControl(Map<String, dynamic> message) async {}
  @override
  Stream<TransportStatus> get status => const Stream.empty();
}
