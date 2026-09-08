import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart' as fs;

/// Microphone lifecycle for PTT. flutter_sound 9.x pitfalls covered here:
/// - openRecorder must happen exactly once per instance ("already
///   initialized" on double-open), so we hold one instance for the app
///   lifetime and gate open with a flag instead of isRecording.
/// - startRecorder can fail after a half-open state; we close+rebuild the
///   instance and retry once, then surface the error.
/// - stopRecorder can hang; we time-box it and force-recover.
/// - A freshly started recorder can be alive but produce no data (fake
///   start); a 300ms byte watchdog detects it and recovers.
class MicController {
  static const int sampleRate = 16000;
  static const int channels = 1;

  fs.FlutterSoundRecorder? _recorder;
  bool _opened = false;
  bool _recording = false;
  StreamController<Uint8List>? _sink;
  Timer? _byteWatchdog;

  bool get recording => _recording;

  /// Start capture; PCM16 bytes flow to [onPcm]. Throws [MicException] on
  /// failure (caller should mark the turn errored).
  Future<void> start(void Function(Uint8List) onPcm) async {
    if (_recording) return;
    await _ensureOpen();

    try {
      await _startRecorderStream(onPcm);
    } catch (e) {
      // half-open state — rebuild and retry once
      try {
        await _recover();
        await _startRecorderStream(onPcm);
      } catch (e2) {
        await _closeSink();
        throw MicException('start failed: $e2');
      }
    }

    _recording = true;

    // Fake-start watchdog: no bytes within 300ms means the recorder is
    // wedged; stop and surface so the turn can be retried.
    _byteWatchdog = Timer(const Duration(milliseconds: 300), () {
      if (_recording) {
        _recording = false;
        unawaited(stop());
        throw MicException('recorder produced no data (fake start)');
      }
    });
  }

  Future<void> _startRecorderStream(void Function(Uint8List) onPcm) async {
    final sink = StreamController<Uint8List>();
    _sink = sink;
    final sub = sink.stream.listen((bytes) {
      if (bytes.isEmpty) return;
      _byteWatchdog?.cancel();
      onPcm(bytes);
    });
    try {
      await _recorder!.startRecorder(
        codec: fs.Codec.pcm16,
        toStream: sink.sink,
        sampleRate: sampleRate,
        numChannels: channels,
        enableNoiseSuppression: true,
        enableEchoCancellation: true,
      );
    } catch (e) {
      await sub.cancel();
      rethrow;
    }
  }

  /// Stop capture. Never throws; a hung stopRecorder is force-recovered.
  Future<void> stop() async {
    if (!_recording) return;
    _recording = false;
    _byteWatchdog?.cancel();
    _byteWatchdog = null;
    try {
      await _recorder?.stopRecorder().timeout(const Duration(seconds: 3));
    } catch (_) {
      await _recover();
    }
    await _closeSink();
  }

  Future<void> _closeSink() async {
    await _sink?.close();
    _sink = null;
  }

  Future<void> _ensureOpen() async {
    if (_opened && _recorder != null) return;
    _recorder ??= fs.FlutterSoundRecorder();
    try {
      await _recorder!.openRecorder();
      _opened = true;
    } catch (e) {
      try {
        await _recorder!.closeRecorder();
      } catch (_) {}
      _recorder = fs.FlutterSoundRecorder();
      try {
        await _recorder!.openRecorder();
        _opened = true;
      } catch (e2) {
        throw MicException('open failed (mic permission?): $e2');
      }
    }
  }

  /// Tear down the wedged instance and build a fresh one; the next start
  /// re-opens it.
  Future<void> _recover() async {
    try {
      await _recorder?.closeRecorder().timeout(const Duration(seconds: 2));
    } catch (_) {}
    _recorder = fs.FlutterSoundRecorder();
    _opened = false;
  }

  Future<void> dispose() async {
    _byteWatchdog?.cancel();
    _recording = false;
    await _closeSink();
    try {
      await _recorder?.closeRecorder();
    } catch (_) {}
    _recorder = null;
    _opened = false;
  }
}

class MicException implements Exception {
  final String message;
  MicException(this.message);
  @override
  String toString() => 'MicException: $message';
}
