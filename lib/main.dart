import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_sound/flutter_sound.dart' as fs;
import 'package:shared_preferences/shared_preferences.dart';

import 'bluetooth/bluetooth_spp.dart';

void main() {
  runApp(const RoboguideRemoteApp());
}

class RoboguideRemoteApp extends StatelessWidget {
  const RoboguideRemoteApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Roboguide Remote',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

enum _ConnState { disconnected, connecting, connected, error }

/// 一行对话：一次 PTT 按下→松开即为一个回合。
class ConversationTurn {
  final String id;
  final DateTime startedAt;
  String userText; // ASR final（用户说的话）
  String assistantText; // Pilot 文本（机器人回复）
  String state; // recording | recognizing | thinking | playing | done | error
  String error;

  /// P3 运动标记：当用户指令命中运动意图时记录动作摘要（如
  /// 'motion_request · 前进'）。仅标记展示，绝不自动执行。
  String? action;

  ConversationTurn({required this.id, DateTime? startedAt})
      : startedAt = startedAt ?? DateTime.now(),
        userText = '',
        assistantText = '',
        state = 'recording',
        error = '';

  Map<String, dynamic> toJson() => {
        'id': id,
        'startedAt': startedAt.toIso8601String(),
        'userText': userText,
        'assistantText': assistantText,
        'state': state,
        'error': error,
        if (action != null) 'action': action,
      };

  factory ConversationTurn.fromJson(Map<String, dynamic> json) {
    final turn = ConversationTurn(
      id: json['id'] as String? ?? '',
      startedAt: DateTime.tryParse(json['startedAt'] as String? ?? ''),
    );
    turn.userText = json['userText'] as String? ?? '';
    turn.assistantText = json['assistantText'] as String? ?? '';
    turn.state = json['state'] as String? ?? 'done';
    turn.error = json['error'] as String? ?? '';
    turn.action = json['action'] as String?;
    return turn;
  }
}

/// 一个会话：一组回合（turns 最新在前，insert(0)）。
class ConversationSession {
  final String id;
  final DateTime createdAt;
  String title; // 默认 = 首句 ASR 前 12 字
  DateTime lastActiveAt;
  final List<ConversationTurn> turns;

  ConversationSession({
    required this.id,
    DateTime? createdAt,
    this.title = '新会话',
    DateTime? lastActiveAt,
    List<ConversationTurn>? turns,
  })  : createdAt = createdAt ?? DateTime.now(),
        lastActiveAt = lastActiveAt ?? DateTime.now(),
        turns = turns ?? [];

  Map<String, dynamic> toJson() => {
        'id': id,
        'createdAt': createdAt.toIso8601String(),
        'title': title,
        'lastActiveAt': lastActiveAt.toIso8601String(),
        'turns': turns.map((t) => t.toJson()).toList(),
      };

  factory ConversationSession.fromJson(Map<String, dynamic> json) {
    final rawTurns = json['turns'] as List? ?? [];
    return ConversationSession(
      id: json['id'] as String? ?? '',
      createdAt: DateTime.tryParse(json['createdAt'] as String? ?? ''),
      title: json['title'] as String? ?? '新会话',
      lastActiveAt: DateTime.tryParse(json['lastActiveAt'] as String? ?? ''),
      turns: rawTurns
          .cast<Map<String, dynamic>>()
          .map(ConversationTurn.fromJson)
          .toList(),
    );
  }
}

class _HomePageState extends State<HomePage> {
  static const _prefsSessionsKey = 'roboguide.sessions_v2';
  static const _prefsCurrentKey = 'roboguide.current_session_v2';

  final _spp = BluetoothSpp();
  fs.FlutterSoundRecorder? _recorder;
  bool _recorderOpened = false;
  fs.FlutterSoundPlayer? _player;
  StreamSubscription<List<Int16List>>? _recSub;
  StreamSubscription<SppControlEvent>? _controlSub;

  List<Map<String, dynamic>> _devices = [];
  List<ConversationSession> _sessions = [];
  ConversationSession? _currentSession;
  ConversationTurn? _activeTurn;
  String? _selectedMac;
  _ConnState _state = _ConnState.disconnected;
  bool _micActive = false;
  String _audioState = 'idle';
  int _sentAudioBytes = 0;
  int _receivedAudioBytes = 0;
  Future<void> _playQueue = Future<void>.value();
  String _log = '';

  static const int _sampleRate = 16000;
  static const int _channels = 1;

  List<ConversationTurn> get _turns =>
      _currentSession?.turns ?? const <ConversationTurn>[];

  @override
  void initState() {
    super.initState();
    // subscribe to received bytes -> play
    _spp.onData.listen((data) {
      _receivedAudioBytes += data.length;
      _playQueue = _playQueue.then((_) => _playPcm(data));
    }, onDone: _onStreamClosed, onError: (_) => _onStreamClosed());
    _controlSub = _spp.onControl.listen(_handleControl);
    _loadSessions();
    _loadDevices();
  }

  void _onStreamClosed() {
    if (!mounted) return;
    // Bluetooth link dropped (or app disconnected): never leave a turn
    // stuck in 'recognizing'/'playing' forever.
    final turn = _activeTurn;
    if (turn != null) {
      setState(() {
        turn.state = 'error';
        turn.error = '连接已断开';
        _audioState = 'idle';
        _activeTurn = null;
      });
      _persist();
    } else if (mounted) {
      setState(() => _audioState = 'idle');
    }
    if (_state != _ConnState.disconnected) {
      setState(() => _state = _ConnState.disconnected);
      _appendLog('bluetooth link closed');
    }
  }

  // ── 会话树持久化（shared_preferences，轻量 JSON） ─────────────
  Future<void> _loadSessions() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsSessionsKey);
      if (raw != null && raw.isNotEmpty) {
        final list = (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
        _sessions = list.map(ConversationSession.fromJson).toList();
        // 未来版本格式变化时自动丢弃损坏数据
      }
      final curId = prefs.getString(_prefsCurrentKey);
      if (_sessions.isNotEmpty) {
        _currentSession = _sessions.firstWhere(
          (s) => s.id == curId,
          orElse: () => _sessions.first,
        );
      }
      debugPrint('[SESS] loaded ${_sessions.length} session(s)');
    } catch (e) {
      debugPrint('[SESS] load failed: $e');
      _sessions = [];
    }
    if (mounted) setState(() {});
  }

  void _persist() {
    // fire-and-forget；只保留最近的会话上限，防止 prefs 膨胀
    SharedPreferences.getInstance()
        .then((prefs) {
          final list = _sessions.take(30).map((s) => s.toJson()).toList();
          prefs.setString(_prefsSessionsKey, jsonEncode(list));
          prefs.setString(_prefsCurrentKey, _currentSession?.id ?? '');
        })
        .catchError((e) => debugPrint('[SESS] save failed: $e'));
  }

  // ── 会话操作 ──────────────────────────────────────────────
  ConversationSession _ensureSession() {
    final cur = _currentSession;
    if (cur != null) return cur;
    final s = ConversationSession(
        id: DateTime.now().microsecondsSinceEpoch.toString());
    _sessions.insert(0, s);
    _currentSession = s;
    return s;
  }

  void _newSession() {
    setState(() => _ensureSession());
    _persist();
    debugPrint('[SESS] new session ${_currentSession!.id}');
  }

  void _selectSession(String? id) {
    if (id == null) return;
    final s = _sessions.where((x) => x.id == id).firstOrNull;
    if (s == null) return;
    setState(() {
      _currentSession = s;
      _activeTurn = null; // 切换会话后旧回合状态不再归属当前视图
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
              onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
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
      _activeTurn = null;
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
              child: const Text('取消')),
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
        _activeTurn = null;
      }
    });
    _persist();
  }

  void _openSessionList() {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => _SessionListPage(
        sessions: _sessions,
        currentId: _currentSession?.id,
        onSelect: (s) {
          _selectSession(s.id);
          Navigator.of(context).pop();
        },
        onNewSession: () {
          _newSession();
        },
        onRename: _renameSession,
        onDelete: _deleteSession,
      ),
    ));
  }

  Future<void> _loadDevices() async {
    try {
      final devices = await _spp.pairedDevices();
      setState(() => _devices = devices);
      // default to Thor if present
      final thor = devices.where((d) =>
          (d['address'] as String) == 'F8:3D:C6:91:8D:69' ||
          (d['name'] as String).toLowerCase().contains('roboguide') ||
          (d['name'] as String).toLowerCase().contains('localhost'));
      if (thor.isNotEmpty) {
        setState(() => _selectedMac = thor.first['address'] as String);
      }
      _appendLog('found ${devices.length} paired device(s)');
    } catch (e) {
      _appendLog('load devices failed: $e');
    }
  }

  @override
  void dispose() {
    _recSub?.cancel();
    _controlSub?.cancel();
    _spp.dispose();
    _recorder?.closeRecorder().catchError((e) => debugPrint('[PTT] closeRecorder failed: $e'));
    _player?.closePlayer();
    super.dispose();
  }

  void _appendLog(String line) {
    if (!mounted) return;
    setState(() {
      _log = '${DateTime.now().toString().substring(11, 19)} $line\n$_log'
          .split('\n')
          .take(8)
          .join('\n');
    });
  }

  Future<void> _toggleConnect() async {
    if (_state == _ConnState.connected || _state == _ConnState.connecting) {
      await _spp.disconnect();
      setState(() => _state = _ConnState.disconnected);
      return;
    }
    final mac = _selectedMac;
    if (mac == null) {
      _appendLog('no device selected');
      return;
    }
    setState(() => _state = _ConnState.connecting);
    try {
      final ok = await _spp.connect(mac);
      setState(() => _state = ok ? _ConnState.connected : _ConnState.error);
      _appendLog(ok ? 'connected via Bluetooth SPP: $mac' : 'connect returned false');
    } catch (e) {
      setState(() => _state = _ConnState.error);
      _appendLog('connect failed: $e');
    }
  }

  // ── PTT: capture microphone PCM and send over Bluetooth SPP ─────────
  Future<void> _startTalking() async {
    if (_micActive || _state != _ConnState.connected) return;
    final session = _ensureSession();
    final turn = ConversationTurn(
        id: DateTime.now().microsecondsSinceEpoch.toString());
    session.lastActiveAt = DateTime.now();
    setState(() {
      _activeTurn = turn;
      session.turns.insert(0, turn);
      _audioState = 'starting microphone';
    });
    // P0 fix (root cause of "second utterance freezes at recognizing"):
    // recorder lifecycle is open -> (start -> stop) * N -> close. Never call
    // openRecorder() a second time on the same FlutterSoundRecorder instance:
    // flutter_sound 9.x can throw "already initialized" and the second PTT
    // then sends no audio, leaving the phone stuck at 'recognizing'.
    debugPrint('[PTT] _startTalking micActive=$_micActive conn=$_state');
    _recorder ??= fs.FlutterSoundRecorder();
    if (!_recorderOpened) {
      try {
        await _recorder!.openRecorder();
        _recorderOpened = true;
        debugPrint('[PTT] openRecorder: OK');
      } catch (e) {
        debugPrint('[PTT] openRecorder failed: $e');
        // Retry once with a fresh instance to recover from any half-init state.
        try { await _recorder!.closeRecorder(); } catch (_) {}
        _recorder = fs.FlutterSoundRecorder();
        try {
          await _recorder!.openRecorder();
          _recorderOpened = true;
          debugPrint('[PTT] openRecorder retry: OK');
        } catch (e2) {
          debugPrint('[PTT] openRecorder retry failed: $e2');
          _appendLog('openRecorder failed (mic permission?): $e2');
          setState(() { turn.state = 'error'; turn.error = 'Microphone permission/initialization failed'; _audioState = 'error'; });
          _persist();
          return;
        }
      }
    } else {
      debugPrint('[PTT] openRecorder: already open, skip reopen');
    }
    final sink = StreamController<List<Int16List>>();
    _recSub = sink.stream.listen((chunks) {
      for (final chunk in chunks) {
        final bytes = Uint8List.view(chunk.buffer);
        _sentAudioBytes += bytes.length;
        _spp.writeAudio(bytes).catchError((e) {
          _appendLog('audio write failed: $e');
        });
      }
    });
    try {
      debugPrint('[PTT] startRecorder: calling');
      await _recorder!.startRecorder(
        codec: fs.Codec.pcm16,
        toStreamInt16: sink,
        sampleRate: _sampleRate,
        numChannels: _channels,
        enableNoiseSuppression: true,
        enableEchoCancellation: true,
      );
      debugPrint('[PTT] startRecorder: OK');
    } catch (e) {
      await _recSub?.cancel();
      _recSub = null;
      _appendLog('startRecorder failed: $e');
      debugPrint('[PTT] startRecorder FAILED: $e');
      setState(() { turn.state = 'error'; turn.error = 'Microphone start failed'; _audioState = 'error'; });
      _persist();
      return;
    }
    setState(() { _micActive = true; _audioState = 'recording'; });
    _persist();
  }

  Future<void> _stopTalking() async {
    if (!_micActive) return;
    debugPrint('[PTT] _stopTalking: stopping recorder');
    setState(() { _micActive = false; _audioState = 'finishing'; });
    await _recSub?.cancel();
    _recSub = null;
    try {
      await _recorder?.stopRecorder();
      debugPrint('[PTT] stopRecorder: OK');
    } catch (e) {
      debugPrint('[PTT] stopRecorder FAILED: $e');
    }
    try {
      // P2 上下文延续：mic_end 携带会话树上下文（session_id + 最近历史），
      // Thor 用稳定 session_id 让 Pilot 跨 PTT 保留会话语境；history
      // 注入下次 StartVoiceSession 的 context_json 供审计/未来消费。
      // 保留不带上下文的纯 mic_end 兼容路径。
      final session = _currentSession;
      if (session != null) {
        final history = <Map<String, String>>[];
        // turns 最新在前 -> 取最近 5 个回合（按时间正序），最多 ~10 条消息
        for (final t in session.turns.reversed.take(5)) {
          if (t.userText.isNotEmpty) {
            history.add({'role': 'user', 'text': t.userText});
          }
          if (t.assistantText.isNotEmpty) {
            history.add({'role': 'assistant', 'text': t.assistantText});
          }
        }
        await _spp.writeControl({
          'type': 'mic_end',
          'session_id': session.id,
          'history': history,
        });
        debugPrint('[PTT] mic_end session=${session.id} history=${history.length}');
      } else {
        await _spp.writeControl({'type': 'mic_end'});
      }
    } catch (e) {
      _appendLog('mic_end failed: $e');
    }
    final turn = _activeTurn;
    if (turn != null && turn.state == 'recording') {
      setState(() { turn.state = 'recognizing'; _audioState = 'recognizing'; });
      _persist();
    }
  }

  Future<void> _playPcm(Uint8List frame) async {
    _player ??= fs.FlutterSoundPlayer();
    try {
      if (!_player!.isPlaying) {
        await _player!.openPlayer();
        await _player!.startPlayerFromStream(
          codec: fs.Codec.pcm16,
          interleaved: true,
          numChannels: _channels,
          sampleRate: _sampleRate,
          bufferSize: 1600,
        );
      }
      await _player!.feedUint8FromStream(frame);
      if (mounted && _activeTurn != null && _activeTurn!.state != 'done') {
        setState(() { _activeTurn!.state = 'playing'; _audioState = 'playing'; });
      }
    } catch (e) {
      _appendLog('playback failed: $e');
      if (mounted && _activeTurn != null) {
        setState(() { _activeTurn!.state = 'error'; _activeTurn!.error = 'Speaker playback failed'; _audioState = 'error'; });
        _persist();
      }
    }
  }

  void _handleControl(SppControlEvent event) {
    final value = event.value;
    if (value['type'] != 'voice_event') return;
    final kind = (value['event_kind'] as num?)?.toInt() ?? -1;
    final text = value['text'] as String? ?? '';
    final status = value['status'] as String? ?? '';
    final error = value['error'] as String? ?? '';
    final turn = _activeTurn;
    if (turn == null) return;
    setState(() {
      switch (kind) {
        case 0:
          turn.state = 'recording'; _audioState = 'recording'; break;
        case 1:
          turn.state = 'recording'; _audioState = 'recording'; break;
        case 2:
          turn.state = 'recognizing'; _audioState = 'recognizing'; break;
        case 4:
          if (text.isNotEmpty) turn.userText = text;
          turn.state = 'thinking'; _audioState = 'thinking'; break;
        case 6:
          if (text.isNotEmpty) turn.assistantText += text;
          turn.state = 'thinking'; _audioState = 'thinking'; break;
        case 7:
          turn.state = 'playing'; _audioState = 'playing'; break;
        case 8:
          turn.state = 'playing'; _audioState = 'playing'; break;
        case 9:
          turn.state = 'done'; _audioState = 'idle'; _activeTurn = null; break;
        case 10:
          turn.state = 'error'; turn.error = error.isNotEmpty ? error : status; _audioState = 'error'; break;
      }
      if (kind == 4 && text.isNotEmpty && turn.userText.isEmpty) turn.userText = text;
    });
    // 会话标题默认取首句 ASR 前 12 字
    if (kind == 4 && text.isNotEmpty) {
      final s = _currentSession;
      if (s != null && (s.title == '新会话' || s.title.isEmpty)) {
        s.title = text.length > 12 ? text.substring(0, 12) : text;
      }
      if (s != null) s.lastActiveAt = DateTime.now();
    }
    if (kind == 4 || kind == 6 || kind == 7 || kind == 8 || kind == 9 || kind == 10) {
      _persist();
    }
  }

  @override
  Widget build(BuildContext context) {
    final connected = _state == _ConnState.connected;
    final sessions = _sessions;
    final current = _currentSession;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Roboguide Remote'),
        actions: [
          IconButton(
            onPressed: _openSessionList,
            icon: const Icon(Icons.forum_outlined),
            tooltip: '会话列表',
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // ── 会话选择器（仿 robonix-client Session 下拉）──
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    initialValue: current?.id,
                    isExpanded: true,
                    hint: const Text('选择会话'),
                    items: sessions
                        .map((s) => DropdownMenuItem(
                              value: s.id,
                              child: Text(
                                s.title,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ))
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
                  onPressed: current == null ? null : _renameSession,
                  icon: const Icon(Icons.edit_outlined),
                  tooltip: '重命名会话',
                ),
                IconButton(
                  onPressed: _turns.isEmpty ? null : _clearSessionTurns,
                  icon: const Icon(Icons.delete_sweep_outlined),
                  tooltip: '清空当前会话',
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    initialValue: _selectedMac,
                    isExpanded: true,
                    items: _devices
                        .map((d) => DropdownMenuItem(
                              value: d['address'] as String,
                              child: Text(
                                '${d['name']} (${d['address']})',
                                overflow: TextOverflow.ellipsis,
                              ),
                            ))
                        .toList(),
                    onChanged: connected
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
                  onPressed: connected ? null : _loadDevices,
                  icon: const Icon(Icons.refresh),
                  tooltip: '刷新设备',
                ),
                const SizedBox(width: 4),
                FilledButton.icon(
                  onPressed: _toggleConnect,
                  icon: Icon(connected
                      ? Icons.bluetooth_disabled
                      : Icons.bluetooth),
                  label: Text(connected ? '断开' : '连接'),
                ),
              ],
            ),
            const SizedBox(height: 10),
            _StatusCard(
              state: _state,
              micActive: _micActive,
              audioState: _audioState,
              sentBytes: _sentAudioBytes,
              receivedBytes: _receivedAudioBytes,
            ),
            const SizedBox(height: 10),
            _PttButton(
              enabled: connected,
              talking: _micActive,
              onPressStart: _startTalking,
              onPressEnd: _stopTalking,
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: Text(
                    current == null
                        ? '尚无会话'
                        : '会话：${current.title}（${current.turns.length} 回合）',
                    style: Theme.of(context).textTheme.titleSmall,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            Expanded(
              child: _turns.isEmpty
                  ? Center(
                      child: Text(
                        current == null
                            ? '点击 ➕ 新建会话，按住下方按钮开始说话'
                            : '暂无对话，按住下方按钮开始说话',
                      ),
                    )
                  : ListView.builder(
                      itemCount: _turns.length,
                      itemBuilder: (context, index) {
                        final turn = _turns[index];
                        return _Bubble(
                          turn: turn,
                          onDelete: () => _removeTurn(turn),
                        );
                      },
                    ),
            ),
            if (_log.isNotEmpty)
              SizedBox(
                height: 58,
                child: Text(_log,
                    style: const TextStyle(
                        fontFamily: 'monospace', fontSize: 10)),
              ),
          ],
        ),
      ),
    );
  }

  void _removeTurn(ConversationTurn turn) {
    final s = _currentSession;
    if (s == null) return;
    setState(() {
      s.turns.remove(turn);
      if (_activeTurn == turn) _activeTurn = null;
    });
    _persist();
  }
}

// ── 状态卡（连接/音频状态） ────────────────────────────────
class _StatusCard extends StatelessWidget {
  final _ConnState state;
  final bool micActive;
  final String audioState;
  final int sentBytes;
  final int receivedBytes;
  const _StatusCard({
    required this.state,
    required this.micActive,
    required this.audioState,
    required this.sentBytes,
    required this.receivedBytes,
  });

  @override
  Widget build(BuildContext context) {
    final color = switch (state) {
      _ConnState.connected => Colors.green,
      _ConnState.connecting => Colors.orange,
      _ConnState.error => Colors.red,
      _ConnState.disconnected => Colors.grey,
    };
    final label = switch (state) {
      _ConnState.connected => 'Connected',
      _ConnState.connecting => 'Connecting',
      _ConnState.error => 'Error',
      _ConnState.disconnected => 'Disconnected',
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(Icons.circle, size: 12, color: color),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  'Bridge: $label',
                  style: Theme.of(context).textTheme.titleMedium,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (micActive)
                const Chip(
                  label: Text('MIC ACTIVE'),
                  backgroundColor: Colors.redAccent,
                  labelStyle: TextStyle(color: Colors.white, fontSize: 11),
                  visualDensity: VisualDensity.compact,
                ),
            ]),
            const SizedBox(height: 2),
            Text(
              '$audioState  TX ${sentBytes}B / RX ${receivedBytes}B',
              style: const TextStyle(fontSize: 11),
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }
}

// ── 消息气泡（你=右 / 机器人=左） ──────────────────────────
class _Bubble extends StatelessWidget {
  final ConversationTurn turn;
  final VoidCallback onDelete;
  const _Bubble({required this.turn, required this.onDelete});

  String get _time {
    final t = turn.startedAt;
    return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 用户消息（右对齐）
          if (turn.userText.isNotEmpty)
            Align(
              alignment: Alignment.centerRight,
              child: Container(
                constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.78),
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(turn.userText),
              ),
            ),
          // 运动标记（P3：仅标记展示，不自动执行）
          if (turn.action != null)
            Align(
              alignment: Alignment.centerRight,
              child: Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Chip(
                  avatar: const Icon(Icons.directions_walk, size: 16),
                  label: Text('运动指令 · ${turn.action}',
                      style: const TextStyle(fontSize: 11)),
                  backgroundColor: Colors.amber.shade100,
                  visualDensity: VisualDensity.compact,
                ),
              ),
            ),
          // 机器人消息（左对齐）
          if (turn.assistantText.isNotEmpty)
            Align(
              alignment: Alignment.centerLeft,
              child: Container(
                margin: const EdgeInsets.only(top: 2),
                constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.78),
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(turn.assistantText),
              ),
            ),
          if (turn.userText.isEmpty && turn.assistantText.isEmpty && turn.error.isEmpty)
            const Padding(
              padding: EdgeInsets.only(bottom: 4),
              child: Text('正在等待语音结果…',
                  style: TextStyle(fontSize: 12, color: Colors.grey)),
            ),
          if (turn.error.isNotEmpty)
            Text(
              turn.error,
              style: TextStyle(color: theme.colorScheme.error, fontSize: 12),
            ),
          // 状态行：时间 + 状态 chip + 删除
          Row(
            mainAxisAlignment: turn.userText.isNotEmpty
                ? MainAxisAlignment.end
                : MainAxisAlignment.start,
            children: [
              Text(_time,
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: Colors.grey)),
              const SizedBox(width: 6),
              Chip(
                label: Text(turn.state,
                    style: const TextStyle(fontSize: 10)),
                visualDensity: VisualDensity.compact,
                padding: EdgeInsets.zero,
              ),
              IconButton(
                onPressed: onDelete,
                icon: const Icon(Icons.delete_outline, size: 16),
                tooltip: '删除此回合',
                visualDensity: VisualDensity.compact,
                padding: EdgeInsets.zero,
                constraints:
                    const BoxConstraints(minWidth: 28, minHeight: 28),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ── 会话列表页 ────────────────────────────────────────────
class _SessionListPage extends StatelessWidget {
  final List<ConversationSession> sessions;
  final String? currentId;
  final ValueChanged<ConversationSession> onSelect;
  final VoidCallback onNewSession;
  final Future<void> Function(ConversationSession) onRename;
  final Future<void> Function(ConversationSession) onDelete;

  const _SessionListPage({
    required this.sessions,
    required this.currentId,
    required this.onSelect,
    required this.onNewSession,
    required this.onRename,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('会话列表'),
        actions: [
          IconButton(
            onPressed: onNewSession,
            icon: const Icon(Icons.add_circle_outline),
            tooltip: '新建会话',
          ),
        ],
      ),
      body: sessions.isEmpty
          ? const Center(child: Text('暂无会话，点击右上角 ➕ 新建'))
          : ListView.builder(
              itemCount: sessions.length,
              itemBuilder: (context, index) {
                final s = sessions[index];
                final last = s.turns.isEmpty
                    ? ''
                    : s.turns.first.assistantText.isNotEmpty
                        ? s.turns.first.assistantText
                        : s.turns.first.userText;
                final selected = s.id == currentId;
                final time =
                    '${s.lastActiveAt.month}/${s.lastActiveAt.day} '
                    '${s.lastActiveAt.hour.toString().padLeft(2, '0')}:'
                    '${s.lastActiveAt.minute.toString().padLeft(2, '0')}';
                return Card(
                  child: ListTile(
                    onTap: () => onSelect(s),
                    onLongPress: () => onRename(s),
                    leading: CircleAvatar(
                      backgroundColor: selected
                          ? Theme.of(context).colorScheme.primary
                          : null,
                      child: Icon(
                        Icons.chat_bubble_outline,
                        size: 20,
                        color: selected
                            ? Theme.of(context).colorScheme.onPrimary
                            : null,
                      ),
                    ),
                    title: Row(
                      children: [
                        Expanded(
                          child: Text(
                            s.title,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontWeight: selected
                                  ? FontWeight.bold
                                  : FontWeight.normal,
                            ),
                          ),
                        ),
                        if (selected)
                          const Padding(
                            padding: EdgeInsets.only(left: 6),
                            child: Text('当前',
                                style: TextStyle(
                                    fontSize: 10, color: Colors.teal)),
                          ),
                      ],
                    ),
                    subtitle: Text(
                      s.turns.isEmpty
                          ? '$time · 空会话'
                          : '$time · ${s.turns.length} 回合 · ${last.length > 24 ? last.substring(0, 24) : last}',
                      overflow: TextOverflow.ellipsis,
                      maxLines: 1,
                    ),
                    trailing: IconButton(
                      onPressed: () => onDelete(s),
                      icon: const Icon(Icons.delete_outline),
                      tooltip: '删除会话（长按可重命名）',
                    ),
                  ),
                );
              },
            ),
    );
  }
}

// ── PTT 按钮 ──────────────────────────────────────────────
class _PttButton extends StatefulWidget {
  final bool enabled;
  final bool talking;
  final VoidCallback onPressStart;
  final VoidCallback onPressEnd;
  const _PttButton({
    required this.enabled,
    required this.talking,
    required this.onPressStart,
    required this.onPressEnd,
  });

  @override
  State<_PttButton> createState() => _PttButtonState();
}

class _PttButtonState extends State<_PttButton> {
  bool _pressed = false;

  void _handleDown() {
    if (!widget.enabled) return;
    setState(() => _pressed = true);
    widget.onPressStart();
  }

  void _handleUp() {
    if (!_pressed) return;
    setState(() => _pressed = false);
    widget.onPressEnd();
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.talking || _pressed;
    final color = !widget.enabled
        ? Colors.grey
        : active
            ? Colors.redAccent
            : Colors.teal;
    return Listener(
      onPointerDown: (_) => _handleDown(),
      onPointerUp: (_) => _handleUp(),
      onPointerCancel: (_) => _handleUp(),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 120),
        height: 72,
        decoration: BoxDecoration(
          color: color.withValues(alpha: active ? 0.9 : 0.15),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: color, width: 2),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(active ? Icons.mic : Icons.mic_none, size: 28, color: color),
            const SizedBox(width: 8),
            Text(
              !widget.enabled
                  ? 'Connect first'
                  : active
                      ? 'Speaking...'
                      : 'Hold to Talk',
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}