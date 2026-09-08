import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

import '../grpc/liaison_client.dart';
import '../grpc/liaison_codec.dart';
import 'robot_transport.dart';

/// Network debug transport: the official robonix split over Tailscale/LAN.
///
///   audio plane  — WebSocket ws://host:60002/client (reverse-audio bridge,
///                  bare PCM binary up/down + JSON text control)
///   event plane  — Liaison gRPC StartVoiceSession (server-streaming
///                  VoiceEvents) + FinishVoiceCapture, exactly what
///                  thor_spp_server.py does for the SPP path.
///
/// One voice session per PTT press (same as SPP): the first audio frame
/// after press-to-talk starts the session; releasing sends mic_end to the
/// bridge AND FinishVoiceCapture to Liaison so ASR finalizes immediately.
class WsTransport implements RobotTransport {
  final String host;
  final int bridgePort;
  final int liaisonPort;

  final String userId;
  final Duration recordWindow;

  WebSocketChannel? _channel;
  StreamSubscription? _wsSub;
  LiaisonClient? _liaison;
  StreamSubscription<Uint8List>? _voiceSub;
  bool _connected = false;

  final _statusCtrl = StreamController<TransportStatus>.broadcast();
  final _audioCtrl = StreamController<Uint8List>.broadcast();
  final _controlCtrl = StreamController<Map<String, dynamic>>.broadcast();

  WsTransport({
    required this.host,
    this.bridgePort = 60002,
    this.liaisonPort = 50081,
    this.userId = 'local:roboguide-ws',
    this.recordWindow = const Duration(seconds: 30),
  });

  @override
  TransportMode get mode => TransportMode.ws;

  @override
  Stream<TransportStatus> get status => _statusCtrl.stream;

  @override
  Stream<Uint8List> get audio => _audioCtrl.stream;

  @override
  Stream<Map<String, dynamic>> get control => _controlCtrl.stream;

  @override
  bool get connected => _connected;

  @override
  Future<void> connect() async {
    _statusCtrl.add(const TransportStatus(TransportStatusKind.connecting));
    final uri = Uri.tryParse('ws://$host:$bridgePort/client');
    if (uri == null || uri.host.isEmpty) {
      _statusCtrl.add(const TransportStatus(
        TransportStatusKind.disconnected,
        reason: '无效地址',
      ));
      throw StateError('ws invalid host: "$host"');
    }
    final channel = WebSocketChannel.connect(uri);
    _channel = channel;
    try {
      await channel.ready.timeout(const Duration(seconds: 5));
    } catch (e) {
      _channel = null;
      final detail = e.toString();
      _statusCtrl.add(TransportStatus(
        TransportStatusKind.disconnected,
        // Surface the target so the UI log pinpoints config vs network.
        reason: '连不上 $host:$bridgePort — 检查手机是否连了 Tailscale'
            '($detail)',
      ));
      throw StateError('ws bridge connect failed: $e');
    }
    _wsSub = channel.stream.listen(
      _onWsMessage,
      onDone: _onBridgeClosed,
      onError: (Object e) => _onBridgeError(e),
      cancelOnError: true,
    );
    _connected = true;
    _statusCtrl.add(const TransportStatus(TransportStatusKind.connected));
  }

  void _onWsMessage(dynamic message) {
    if (message is List<int>) {
      _audioCtrl.add(Uint8List.fromList(message));
    } else if (message is String) {
      try {
        final value = jsonDecode(message);
        if (value is Map<String, dynamic>) _controlCtrl.add(value);
      } catch (_) {
        // ignore malformed control text
      }
    }
  }

  void _onBridgeClosed() {
    if (!_connected) return;
    _connected = false;
    _statusCtrl.add(const TransportStatus(
      TransportStatusKind.disconnected,
      reason: 'bridge stream closed',
    ));
  }

  void _onBridgeError(Object e) {
    if (!_connected) return;
    _connected = false;
    _statusCtrl.add(TransportStatus(
      TransportStatusKind.disconnected,
      reason: 'bridge error: $e',
    ));
  }

  @override
  Future<void> disconnect() async {
    _connected = false;
    _voiceSub?.cancel();
    _voiceSub = null;
    await _wsSub?.cancel();
    _wsSub = null;
    try {
      await _channel?.sink.close(ws_status.normalClosure)
          .timeout(const Duration(seconds: 2));
    } catch (_) {}
    _channel = null;
    unawaited(_liaison?.close());
    _liaison = null;
    _statusCtrl.add(const TransportStatus(
      TransportStatusKind.disconnected,
      reason: 'user',
    ));
  }

  // ── PTT voice-session driving ───────────────────────────────────────

  /// Called on PTT press (before the first audio frame goes out): starts a
  /// fresh Liaison voice session and forwards its events as
  /// `{type:'voice_event', ...}` control messages, byte-compatible with the
  /// SPP path's framing.
  Future<void> beginVoiceSession(String sessionId, String historyJson) async {
    final session = _liaison ??= LiaisonClient(host: host, port: liaisonPort);
    _voiceSub?.cancel();
    _voiceSub = session
        .startVoiceSession(
          sessionId: sessionId,
          clientUserId: userId,
          recordSeconds: recordWindow.inSeconds,
          language: 'zh',
          ttsEnabled: true,
          contextJson: historyJson.isEmpty
              ? '{"source":"roboguide-ws"}'
              : historyJson,
        )
        .listen(
          (bytes) {
            final event = decodeVoiceEventResponse(bytes);
            _controlCtrl.add({
              'type': 'voice_event',
              'event_kind': event.eventKind,
              'text': event.text,
              'status': event.statusMessage,
              'user_id': event.userId,
              'error': event.error,
              'session_id': event.sessionId,
            });
          },
          onError: (Object e) {
            _controlCtrl.add({
              'type': 'voice_event',
              'event_kind': 10,
              'error': 'voice session failed: $e',
            });
          },
          cancelOnError: true,
        );
  }

  /// Called on PTT release: mic_end to the bridge, FinishVoiceCapture to
  /// Liaison so ASR finalizes without waiting out record_seconds.
  Future<void> endVoiceCapture(String sessionId) async {
    await sendControl({'type': 'mic_end', 'stream_id': ''});
    try {
      final liaison = _liaison;
      if (liaison != null) {
        await liaison.finishVoiceCapture(sessionId);
      }
    } catch (_) {
      // finish rejected (e.g. no active session) — the voice stream still
      // flushes its own asr_final/session_done events.
    }
  }

  @override
  Future<void> sendAudio(Uint8List pcm) async {
    final channel = _channel;
    if (channel == null || !_connected) return;
    channel.sink.add(pcm);
  }

  @override
  Future<void> sendControl(Map<String, dynamic> message) async {
    final channel = _channel;
    if (channel == null || !_connected) return;
    channel.sink.add(jsonEncode(message));
  }

  @override
  void dispose() {
    _voiceSub?.cancel();
    _wsSub?.cancel();
    _channel = null;
    _connected = false;
    unawaited(_liaison?.close());
    _liaison = null;
    _statusCtrl.close();
    _audioCtrl.close();
    _controlCtrl.close();
  }
}
