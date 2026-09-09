import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../transport/spp_framer.dart';

/// Offline capture of exactly the bytes that a real client would transmit
/// over Bluetooth SPP, so the client can be debugged up to "data ready"
/// without a link, and the robot server can mock-consume the same payload.
///
/// One [CaptureSession] per PTT, three files under `{base}/captures/cap_<ts>/`:
///  - stream.sppwire — the exact SPP wire bytes: RGAD audio frames plus a
///    final RGCT mic_end frame. Byte-identical to a live SPP send, so the
///    server-side mock feeds it straight into its receiver's framing.
///  - audio.pcm      — the bare PCM16/16kHz/mono payload, for client-side
///    local debugging (Audacity, a local recognizer).
///  - meta.json      — recording metadata, timestamps, byte counts.
///
/// Everything is best-effort: IO failures route to [onError] and never throw
/// into the hot mic callback. When disabled, [begin] returns null and the app
/// behaves exactly as before.
class CaptureController {
  Directory baseDir;
  bool enabled;
  final void Function(String message)? onError;
  CaptureSession? _active;

  CaptureController({
    required this.baseDir,
    this.enabled = false,
    this.onError,
  });

  bool get hasActive => _active != null;

  void _err(String message) => onError?.call(message);

  Future<CaptureSession?> begin({
    required String sessionId,
    required String mode,
  }) async {
    if (!enabled || _active != null) return null;
    try {
      final session =
          CaptureSession(dir: _freshDir(), sessionId: sessionId, mode: mode);
      _active = session;
      return session;
    } catch (e) {
      _err('捕获开始失败: $e');
      return null;
    }
  }

  /// Close the active capture (writes the RGCT mic_end frame + files + meta).
  /// Returns the capture folder path for logging, or null when none.
  Future<String?> endActive() async {
    final session = _active;
    _active = null;
    if (session == null) return null;
    try {
      await session.end();
      return session.dir.path;
    } catch (e) {
      _err('捕获结束失败: $e');
      return null;
    }
  }

  Directory _freshDir() {
    final root = Directory('${baseDir.path}/captures')..createSync(recursive: true);
    final stamp = _stamp(DateTime.now());
    var dir = Directory('${root.path}/cap_$stamp');
    var n = 1;
    while (dir.existsSync()) {
      dir = Directory('${root.path}/cap_${stamp}_$n');
      n++;
    }
    dir.createSync(recursive: true);
    return dir;
  }

  Future<void> dispose() async {
    await endActive();
  }
}

/// One PTT's capture files, all inside a single folder. Bytes are buffered in
/// memory during the hot mic callback and flushed once at [end].
class CaptureSession {
  final Directory dir;
  final String sessionId;
  final String mode;
  final DateTime startedAt;
  final BytesBuilder _wire = BytesBuilder(copy: false);
  final BytesBuilder _pcm = BytesBuilder(copy: false);
  int chunkCount = 0;
  int pcmBytes = 0;
  int wireBytes = 0;
  bool _ended = false;

  CaptureSession({
    required this.dir,
    required this.sessionId,
    required this.mode,
  }) : startedAt = DateTime.now();

  /// Append one recorded PCM chunk, encoded exactly as it leaves a real SPP
  /// client: an RGAD audio frame on the wire + the bare bytes for local use.
  void onPcm(Uint8List pcm) {
    if (pcm.isEmpty || _ended) return;
    final framed = SppFramer.encodeAudio(pcm);
    _wire.add(framed);
    _pcm.add(pcm);
    chunkCount++;
    pcmBytes += pcm.length;
    wireBytes += framed.length;
  }

  /// Close the turn: append the RGCT mic_end control frame (the same bytes a
  /// real client writes when the button is released), flush files and finalize
  /// meta.json. Idempotent.
  Future<void> end() async {
    if (_ended) return;
    _ended = true;
    final endFrame = SppFramer.encodeControl({'type': 'mic_end', 'stream_id': ''});
    wireBytes += endFrame.length;
    _wire.add(endFrame);
    final endedAt = DateTime.now();
    await File('${dir.path}/stream.sppwire').writeAsBytes(_wire.toBytes());
    await File('${dir.path}/audio.pcm').writeAsBytes(_pcm.toBytes());
    await File('${dir.path}/meta.json').writeAsString(jsonEncode(<String, dynamic>{
      'version': 1,
      'sessionId': sessionId,
      'mode': mode,
      'codec': 'pcm16',
      'sampleRate': 16000,
      'channels': 1,
      'chunkCount': chunkCount,
      'pcmBytes': pcmBytes,
      'wireBytes': wireBytes,
      'micEnd': true,
      'source': 'roboguide-remote',
      'startedAt': startedAt.toIso8601String(),
      'endedAt': endedAt.toIso8601String(),
    }));
  }
}

String _stamp(DateTime t) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${t.year}${two(t.month)}${two(t.day)}-'
      '${two(t.hour)}${two(t.minute)}${two(t.second)}';
}