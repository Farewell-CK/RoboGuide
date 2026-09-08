import 'dart:async';
import 'dart:typed_data';

import 'package:grpc/grpc.dart';

import 'liaison_codec.dart';

/// Minimal contract-based Liaison gRPC client for the WS debug transport.
/// Same method paths as robonix's official clients:
///   robonix.contracts.RobonixSystemLiaisonVoice/StartVoiceSession
///   robonix.contracts.RobonixSystemLiaisonVoiceFinish/FinishVoiceCapture
///
/// Uses raw-bytes marshalling with hand-rolled codec (liaison_codec.dart);
/// no generated stubs required.
class LiaisonClient {
  final String host;
  final int port;
  ClientChannel? _channel;

  LiaisonClient({required this.host, this.port = 50081});

  static final ClientMethod<Uint8List, Uint8List> _voiceMethod =
      ClientMethod<Uint8List, Uint8List>(
    '/robonix.contracts.RobonixSystemLiaisonVoice/StartVoiceSession',
    (value) => value,
    (bytes) => Uint8List.fromList(bytes),
  );

  static final ClientMethod<Uint8List, Uint8List> _finishMethod =
      ClientMethod<Uint8List, Uint8List>(
    '/robonix.contracts.RobonixSystemLiaisonVoiceFinish/FinishVoiceCapture',
    (value) => value,
    (bytes) => Uint8List.fromList(bytes),
  );

  ClientChannel _channelFor() {
    final c = _channel;
    if (c != null) return c;
    return _channel = ClientChannel(
      host,
      port: port,
      options: const ChannelOptions(credentials: ChannelCredentials.insecure()),
    );
  }

  /// Server-streaming voice session; one raw response item per VoiceEvent.
  Stream<Uint8List> startVoiceSession({
    required String sessionId,
    required String clientUserId,
    int recordSeconds = 30,
    String language = 'zh',
    bool ttsEnabled = true,
    String contextJson = '',
  }) {
    final channel = _channelFor();
    final request = encodeStartVoiceSessionRequest(
      sessionId: sessionId,
      clientUserId: clientUserId,
      recordSeconds: recordSeconds,
      language: language,
      ttsEnabled: ttsEnabled,
      contextJson: contextJson,
    );
    final call = channel.createCall(
      _voiceMethod,
      Stream.value(request),
      CallOptions(timeout: Duration(seconds: recordSeconds + 45)),
    );
    return call.response;
  }

  /// Unary early-finish of the running voice session.
  Future<FinishResult> finishVoiceCapture(String sessionId) async {
    final channel = _channelFor();
    final call = channel.createCall(
      _finishMethod,
      Stream.value(encodeFinishVoiceCaptureRequest(sessionId)),
      CallOptions(),
    );
    final bytes = await call.response.single;
    return decodeFinishVoiceCaptureResponse(bytes);
  }

  Future<void> close() async {
    await _channel?.shutdown();
    _channel = null;
  }
}
