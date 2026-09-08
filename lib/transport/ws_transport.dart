import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

import 'robot_transport.dart';

/// Debug fallback transport: direct WebSocket to the robot's reverse-audio
/// bridge (robonix primitive-audio-client-bridge), i.e. the official
/// AudioBridge wire format: bare PCM binary frames + JSON text control.
///
/// NOTE: unlike SPP, nothing on this path drives a Liaison voice session —
/// the bridge accepts PCM/control but ASR/pilot events only flow while a
/// voice session is active server-side. Treat WS mode as an audio self-test
/// (PTT → PCM → bridge → robot loopback → playback).
class WsTransport implements RobotTransport {
  final String host;
  final int port;

  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  bool _connected = false;

  final _statusCtrl = StreamController<TransportStatus>.broadcast();
  final _audioCtrl = StreamController<Uint8List>.broadcast();
  final _controlCtrl = StreamController<Map<String, dynamic>>.broadcast();

  WsTransport({required this.host, this.port = 60002});

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
    final channel = WebSocketChannel.connect(Uri.parse('ws://$host:$port/client'));
    _channel = channel;
    try {
      await channel.ready.timeout(const Duration(seconds: 5));
    } catch (e) {
      _channel = null;
      _statusCtrl.add(
        TransportStatus(TransportStatusKind.disconnected, reason: 'connect failed: $e'),
      );
      throw StateError('ws connect failed: $e');
    }
    _sub = channel.stream.listen(
      _onMessage,
      onDone: _onClosed,
      onError: _onError,
      cancelOnError: true,
    );
    _connected = true;
    _statusCtrl.add(const TransportStatus(TransportStatusKind.connected));
  }

  void _onMessage(dynamic message) {
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

  void _onClosed() {
    _connected = false;
    _statusCtrl.add(
      const TransportStatus(TransportStatusKind.disconnected, reason: 'stream closed'),
    );
  }

  void _onError(Object e) {
    _connected = false;
    _statusCtrl.add(TransportStatus(TransportStatusKind.disconnected, reason: 'error: $e'));
  }

  @override
  Future<void> disconnect() async {
    _connected = false;
    await _sub?.cancel();
    _sub = null;
    try {
      await _channel?.sink.close(ws_status.normalClosure).timeout(const Duration(seconds: 2));
    } catch (_) {}
    _channel = null;
    _statusCtrl.add(const TransportStatus(TransportStatusKind.disconnected, reason: 'user'));
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
    _sub?.cancel();
    _channel = null;
    _connected = false;
    _statusCtrl.close();
    _audioCtrl.close();
    _controlCtrl.close();
  }
}
