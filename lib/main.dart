import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_sound/flutter_sound.dart' as fs;

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

class _ConversationTurn {
  final String id;
  final DateTime startedAt;
  String userText;
  String assistantText;
  String state;
  String error;

  _ConversationTurn({required this.id})
      : startedAt = DateTime.now(),
        userText = '',
        assistantText = '',
        state = 'recording',
        error = '';
}

class _HomePageState extends State<HomePage> {
  final _spp = BluetoothSpp();
  fs.FlutterSoundRecorder? _recorder;
  fs.FlutterSoundPlayer? _player;
  StreamSubscription<List<Int16List>>? _recSub;
  StreamSubscription<SppControlEvent>? _controlSub;

  List<Map<String, dynamic>> _devices = [];
  final List<_ConversationTurn> _turns = [];
  _ConversationTurn? _activeTurn;
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

  @override
  void initState() {
    super.initState();
    // subscribe to received bytes -> play
    _spp.onData.listen((data) {
      _receivedAudioBytes += data.length;
      _playQueue = _playQueue.then((_) => _playPcm(data));
    }, onDone: _onStreamClosed, onError: (_) => _onStreamClosed());
    _controlSub = _spp.onControl.listen(_handleControl);
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
    } else if (mounted) {
      setState(() => _audioState = 'idle');
    }
    if (_state != _ConnState.disconnected) {
      setState(() => _state = _ConnState.disconnected);
      _appendLog('bluetooth link closed');
    }
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
    _recorder?.closeRecorder();
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
    final turn = _ConversationTurn(id: DateTime.now().microsecondsSinceEpoch.toString());
    setState(() {
      _activeTurn = turn;
      _turns.insert(0, turn);
      _audioState = 'starting microphone';
    });
    _recorder ??= fs.FlutterSoundRecorder();
    try {
      await _recorder!.openRecorder();
    } catch (e) {
      _appendLog('openRecorder failed (mic permission?): $e');
      setState(() { turn.state = 'error'; turn.error = 'Microphone permission/initialization failed'; _audioState = 'error'; });
      return;
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
      await _recorder!.startRecorder(
        codec: fs.Codec.pcm16,
        toStreamInt16: sink,
        sampleRate: _sampleRate,
        numChannels: _channels,
        enableNoiseSuppression: true,
        enableEchoCancellation: true,
      );
    } catch (e) {
      await _recSub?.cancel();
      _recSub = null;
      _appendLog('startRecorder failed: $e');
      setState(() { turn.state = 'error'; turn.error = 'Microphone start failed'; _audioState = 'error'; });
      return;
    }
    setState(() { _micActive = true; _audioState = 'recording'; });
  }

  Future<void> _stopTalking() async {
    if (!_micActive) return;
    setState(() { _micActive = false; _audioState = 'finishing'; });
    await _recSub?.cancel();
    _recSub = null;
    try { await _recorder?.stopRecorder(); } catch (_) {}
    try { await _spp.writeControl({'type': 'mic_end'}); } catch (e) { _appendLog('mic_end failed: $e'); }
    final turn = _activeTurn;
    if (turn != null && turn.state == 'recording') {
      setState(() { turn.state = 'recognizing'; _audioState = 'recognizing'; });
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
  }

  void _clearTurns() => setState(() { _turns.clear(); _activeTurn = null; });
  void _removeTurn(_ConversationTurn turn) => setState(() { _turns.remove(turn); if (_activeTurn == turn) _activeTurn = null; });

  @override
  Widget build(BuildContext context) {
    final connected = _state == _ConnState.connected;
    return Scaffold(
      appBar: AppBar(title: const Text('Roboguide Remote')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<String>(
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
              onChanged: connected ? null : (mac) => setState(() => _selectedMac = mac),
              decoration: const InputDecoration(
                labelText: 'Robot (Bluetooth)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: connected ? null : _loadDevices,
              icon: const Icon(Icons.refresh),
              label: const Text('Refresh devices'),
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: _toggleConnect,
              icon: Icon(connected ? Icons.bluetooth_disabled : Icons.bluetooth),
              label: Text(connected ? 'Disconnect' : 'Connect'),
            ),
            const SizedBox(height: 12),
            _StatusCard(
              state: _state,
              micActive: _micActive,
              audioState: _audioState,
              sentBytes: _sentAudioBytes,
              receivedBytes: _receivedAudioBytes,
            ),
            const SizedBox(height: 12),
            _PttButton(
              enabled: connected,
              talking: _micActive,
              onPressStart: _startTalking,
              onPressEnd: _stopTalking,
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Text('会话记录', style: Theme.of(context).textTheme.titleMedium),
                const Spacer(),
                TextButton(
                  onPressed: _turns.isEmpty ? null : _clearTurns,
                  child: const Text('清空'),
                ),
              ],
            ),
            Expanded(
              child: _turns.isEmpty
                  ? const Center(child: Text('暂无会话，按住下方按钮开始说话'))
                  : ListView.builder(
                      itemCount: _turns.length,
                      itemBuilder: (context, index) {
                        final turn = _turns[index];
                        return _TurnCard(turn: turn, onDelete: () => _removeTurn(turn));
                      },
                    ),
            ),
            if (_log.isNotEmpty)
              SizedBox(
                height: 58,
                child: Text(_log, style: const TextStyle(fontFamily: 'monospace', fontSize: 10)),
              ),
          ],
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final _ConnState state;
  final bool micActive;
  final String audioState;
  final int sentBytes;
  final int receivedBytes;
  const _StatusCard({required this.state, required this.micActive, required this.audioState, required this.sentBytes, required this.receivedBytes});

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

class _TurnCard extends StatelessWidget {
  final _ConversationTurn turn;
  final VoidCallback onDelete;
  const _TurnCard({required this.turn, required this.onDelete});

  @override
  Widget build(BuildContext context) {
    final time = '${turn.startedAt.hour.toString().padLeft(2, '0')}:${turn.startedAt.minute.toString().padLeft(2, '0')}';
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Text(time, style: Theme.of(context).textTheme.labelSmall),
            const SizedBox(width: 8),
            Chip(label: Text(turn.state), visualDensity: VisualDensity.compact),
            const Spacer(),
            IconButton(onPressed: onDelete, icon: const Icon(Icons.delete_outline), tooltip: '删除会话'),
          ]),
          if (turn.userText.isNotEmpty) Text('你：${turn.userText}'),
          if (turn.assistantText.isNotEmpty) Text('机器人：${turn.assistantText}'),
          if (turn.userText.isEmpty && turn.assistantText.isEmpty) const Text('正在等待语音结果…'),
          if (turn.error.isNotEmpty) Text(turn.error, style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ]),
      ),
    );
  }
}

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
