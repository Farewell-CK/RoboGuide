import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

/// Roboguide audio bridge — speaks the robonix reverse-audio protocol.
///
/// Protocol (robot side: primitive-audio-client-bridge, transport: reverse):
///   connect  -&gt; ws://&lt;robot&gt;:60002/client
///   App→robot: binary 1600B frames (16kHz int16 mono PCM, 100ms) while mic active
///   App→robot: {"type":"mic_end","stream_id":""} to stop mic
///   robot→App: binary 1600B frames (speaker PCM to play)
///   robot→App: {"type":"mic_start","stream_id":...} / {"type":"mic_stop",...}
///   robot→App: {"type":"speaker_end"} / {"type":"speaker_stop"}
///   robot→App: {"type":"control_request","id":...,"op":"list_devices"|"select_device","payload":{}}
///   App→robot: {"type":"control_response","id":...,"ok":true,"devices":[...]} (reply)
const int kSampleRate = 16000;
const int kChannels = 1;
const int kFrameBytes = 1600; // 16000/10 * 2 bytes (int16 mono, 100ms)

/// Bridge state, mirroring what the UI needs to render.
enum BridgeState {
  disconnected,
  connecting,
  connected,
  error,
}

/// Result of a device query (list_devices control request).
class AudioDeviceInfo {
  final int id;
  final String name;
  final bool isInput;
  AudioDeviceInfo({required this.id, required this.name, required this.isInput});

  factory AudioDeviceInfo.fromJson(Map<String, dynamic> json) {
    return AudioDeviceInfo(
      id: (json['id'] as num?)?.toInt() ?? 0,
      name: (json['name'] as String?) ?? 'device ${json['id']}',
      isInput: (json['isInput'] as bool?) ?? false,
    );
  }
}

/// Handles the WebSocket connection, mic capture, speaker playback, and the
/// small JSON control protocol. One instance per robot connection.
class AudioBridge {
  final String robotHost;
  final int bridgePort;

  WebSocketChannel? _channel;
  StreamSubscription? _wsSub;
  FlutterSoundRecorder? _recorder;
  FlutterSoundPlayer? _player;
  StreamSubscription? _recStreamSub;
  StreamSubscription? _playerStreamSub;

  final _stateCtrl = StreamController<BridgeState>.broadcast();
  final _micActiveCtrl = StreamController<bool>.broadcast();
  final _levelCtrl = StreamController<double>.broadcast(); // 0..1 mic level

  BridgeState _state = BridgeState.disconnected;
  bool _micActive = false;
  bool _micRequested = false;
  String _activeStreamId = '';
  final List<AudioDeviceInfo> _devices = [];

  AudioBridge({required this.robotHost, this.bridgePort = 60002});

  BridgeState get state => _state;
  bool get micActive => _micActive;
  Stream<BridgeState> get stateStream => _stateCtrl.stream;
  Stream<bool> get micActiveStream => _micActiveCtrl.stream;
  Stream<double> get levelStream => _levelCtrl.stream;
  List<AudioDeviceInfo> get devices => List.unmodifiable(_devices);

  // ── connection ──────────────────────────────────────────────────────
  Future<void> connect() async {
    if (_state == BridgeState.connected || _state == BridgeState.connecting) {
      return;
    }
    _setState(BridgeState.connecting);
    try {
      final uri = Uri.parse('ws://$robotHost:$bridgePort/client');
      _channel = WebSocketChannel.connect(uri);
      _wsSub = _channel!.stream.listen(_onWsMessage, onDone: _onWsClosed,
          onError: _onWsError, cancelOnError: true);
      _setState(BridgeState.connected);
    } catch (e) {
      _setState(BridgeState.error);
      rethrow;
    }
  }

  Future<void> disconnect() async {
    await _stopMic();
    await _closePlayer();
    await _wsSub?.cancel();
    _channel?.sink.close(ws_status.normalClosure);
    _channel = null;
    _setState(BridgeState.disconnected);
  }

  // ── websocket message dispatch ──────────────────────────────────────
  void _onWsMessage(dynamic message) {
    if (message is List<int>) {
      // robot -> App: speaker PCM frame
      _playPcm(Uint8List.fromList(message));
      return;
    }
    if (message is String) {
      _handleControl(jsonDecode(message));
    }
  }

  void _handleControl(Map<String, dynamic> cmd) {
    final type = cmd['type'] as String?;
    switch (type) {
      case 'mic_start':
        _activeStreamId = (cmd['stream_id'] as String?) ?? '';
        _micRequested = true;
        _startMic();
      case 'mic_stop':
        _micRequested = false;
        _stopMic();
      case 'speaker_end':
        _drainPlayer();
      case 'speaker_stop':
        _interruptPlayer();
      case 'control_request':
        _replyControl(cmd);
    }
  }

  // ── mic (App -> robot) ──────────────────────────────────────────────
  Future<void> _startMic() async {
    if (_micActive) return;
    _recorder ??= FlutterSoundRecorder();
    await _recorder!.openRecorder();
    // toStreamInt16 gives us exactly the wire format: 16kHz int16 mono PCM.
    final sink = StreamController<List<Int16List>>();
    _recStreamSub = sink.stream.listen((chunks) {
      for (final chunk in chunks) {
        _onMicData(chunk);
      }
    });
    await _recorder!.startRecorder(
      codec: Codec.pcm16,
      toStreamInt16: sink,
      sampleRate: kSampleRate,
      numChannels: kChannels,
      enableNoiseSuppression: true,
      enableEchoCancellation: true,
    );
    _micActive = true;
    _micActiveCtrl.add(true);
  }

  void _onMicData(Int16List samples) {
    if (samples.isEmpty) return;
    final bytes = Uint8List.view(samples.buffer);
    // Each chunk may not be exactly kFrameBytes — we send whole chunks as-is;
    // the robot bridge consumes raw PCM frames regardless of chunk boundaries.
    _sendBinary(bytes);
    // naive level for UI (RMS of int16 samples)
    var sum = 0;
    for (final s in samples) {
      final v = s < 0 ? -s : s;
      sum += v;
    }
    _levelCtrl.add((sum / samples.length) / 32767.0);
  }

  Future<void> _stopMic() async {
    if (!_micActive) return;
    _micActive = false;
    _micActiveCtrl.add(false);
    _levelCtrl.add(0);
    await _recStreamSub?.cancel();
    _recStreamSub = null;
    try {
      await _recorder?.stopRecorder();
    } catch (_) {}
    if (_micRequested) {
      _micRequested = false; // consumed; don't resend on next stop
      // robot asked us to stop; tell it we ended
      _sendText(jsonEncode({'type': 'mic_end', 'stream_id': _activeStreamId}));
    }
  }

  // ── Push-To-Talk (active, user-initiated) ───────────────────────────
  // Unlike the passive flow (robot sends mic_start), PTT lets the user press
  // and hold to speak. These bridge the same mic path: start capture when
  // pressed, send mic_end when released so the robot (via its mic stream)
  // knows the utterance is done.
  //
  // NOTE: we always send mic_end on release so the robot's ASR can finalize the
  // utterance. Some robot-side flows key off the mic stream ending; keeping
  // this symmetrical with the passive flow is the safe choice.

  /// Open the microphone and start streaming PCM to the robot.
  Future<void> startTalking() async {
    if (!_micActive) {
      _micRequested = true; // treat like a request so stop sends mic_end
      await _startMic();
    }
  }

  /// Stop streaming and signal end-of-utterance to the robot.
  /// (_stopMic already sends mic_end because startTalking set _micRequested.)
  Future<void> stopTalking() async {
    await _stopMic();
  }

  // ── speaker (robot -> App) ──────────────────────────────────────────
  Future<void> _playPcm(Uint8List frame) async {
    _player ??= FlutterSoundPlayer();
    if (!_player!.isPlaying) {
      await _player!.openPlayer();
      await _player!.startPlayerFromStream(
        codec: Codec.pcm16,
        interleaved: true,
        numChannels: kChannels,
        sampleRate: kSampleRate,
        bufferSize: kFrameBytes,
      );
    }
    await _player!.feedUint8FromStream(frame);
  }

  Future<void> _drainPlayer() async {
    // robot says playback finished — wait briefly for buffered tail then stop
    await Future.delayed(const Duration(milliseconds: 200));
    await _closePlayer();
  }

  Future<void> _interruptPlayer() async {
    await _closePlayer();
  }

  Future<void> _closePlayer() async {
    await _playerStreamSub?.cancel();
    _playerStreamSub = null;
    try {
      await _player?.stopPlayer();
    } catch (_) {}
    try {
      await _player?.closePlayer();
    } catch (_) {}
  }

  // ── control_request replies ─────────────────────────────────────────
  Future<void> _replyControl(Map<String, dynamic> cmd) async {
    final id = (cmd['id'] as String?) ?? '';
    final op = (cmd['op'] as String?) ?? '';
    try {
      if (op == 'list_devices') {
        // We cannot enumerate PortAudio devices from Flutter; report the
        // defaults we are actually using.
        final payload = {
          'ok': true,
          'input': 'app-mic',
          'output': 'app-speaker',
          'devices': _devices
              .map((d) => {'id': d.id, 'name': d.name, 'isInput': d.isInput})
              .toList(),
        };
        _sendText(jsonEncode({'type': 'control_response', 'id': id, ...payload}));
      } else if (op == 'select_device') {
        _sendText(jsonEncode({
          'type': 'control_response',
          'id': id,
          'ok': true,
          'selected': (cmd['payload'] as Map<String, dynamic>?)?['device'] ?? '',
        }));
      } else {
        _sendText(jsonEncode({
          'type': 'control_response',
          'id': id,
          'ok': false,
          'error': 'unsupported op: $op',
        }));
      }
    } catch (e) {
      _sendText(jsonEncode({'type': 'control_response', 'id': id, 'ok': false, 'error': '$e'}));
    }
  }

  // ── send helpers ────────────────────────────────────────────────────
  void _sendBinary(Uint8List data) {
    _channel?.sink.add(data);
  }

  void _sendText(String text) {
    _channel?.sink.add(text);
  }

  // ── lifecycle ───────────────────────────────────────────────────────
  void _onWsClosed() {
    _setState(BridgeState.disconnected);
  }

  void _onWsError(Object error) {
    _setState(BridgeState.error);
  }

  void _setState(BridgeState s) {
    _state = s;
    _stateCtrl.add(s);
  }

  void dispose() {
    _stateCtrl.close();
    _micActiveCtrl.close();
    _levelCtrl.close();
  }
}
