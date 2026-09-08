import 'dart:async';
import 'dart:typed_data';

/// Which wire the client uses to reach the robot.
enum TransportMode { spp, ws }

enum TransportStatusKind { connecting, connected, disconnected }

class TransportStatus {
  final TransportStatusKind kind;

  /// Non-empty when [kind] is a disconnect; 'user' means local intent.
  final String reason;
  const TransportStatus(this.kind, {this.reason = ''});
}

/// One transport backend to the robot.
///
/// Contract: audio payloads are always bare PCM16/16kHz/mono chunks; control
/// payloads are decoded JSON maps. Wire framing (RGAD/RGCT for SPP, raw
/// binary/text for the WebSocket bridge) is internal to each backend.
abstract class RobotTransport {
  TransportMode get mode;

  Stream<TransportStatus> get status;
  Stream<Uint8List> get audio;
  Stream<Map<String, dynamic>> get control;

  bool get connected;

  Future<void> connect();
  Future<void> disconnect();
  Future<void> sendAudio(Uint8List pcm);
  Future<void> sendControl(Map<String, dynamic> message);

  void dispose();
}
