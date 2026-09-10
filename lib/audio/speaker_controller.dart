import 'dart:async';
import 'dart:collection';
import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart' as fs;

/// Speaker playback — live streaming, one frame at a time.
///
/// The server throttles SPP at ~32KB/s (0.26s per 8192B frame), which paces
/// our feedUint8FromStream calls at roughly realtime. Earlier "only first
/// chunk played" reports were caused by (a) the server bursting many frames
/// back-to-back while the phone's BT stack dropped all but the first ~33KB,
/// and (b) a flutter_sound streaming quirk under those bursts. With the
/// server throttled and frames arriving steadily, live streaming plays the
/// full utterance with minimal latency.
///
/// A one-shot WAV fallback (replace feed+_pump with accumulate+finishAndPlay)
/// is intentionally NOT wired in: it adds a whole-turn latency the user
/// rejected. Keep `finishAndPlay` below if streaming regresses.
class SpeakerController {
  static const int sampleRate = 16000;
  static const int bufferBytes = 8192; // match the throttled TTS frame size

  final Queue<Uint8List> _queue = Queue<Uint8List>();
  fs.FlutterSoundPlayer? _player;
  bool _playerReady = false;
  bool _pumping = false;
  bool _turnActive = false;

  final _errors = StreamController<String>.broadcast();
  Stream<String> get errors => _errors.stream;

  Future<void> beginTurn() async {
    _turnActive = true;
    _queue.clear();
  }

  void feed(Uint8List pcm) {
    if (!_turnActive) return;
    _queue.add(pcm);
    _pump();
  }

  Future<void> _pump() async {
    if (_pumping) return;
    _pumping = true;
    try {
      while (_queue.isNotEmpty && _turnActive) {
        final frame = _queue.removeFirst();
        print('SPK feed frame ${frame.length}B (q left ${_queue.length})');
        try {
          await _ensureReady();
          await _player!
              .feedUint8FromStream(frame)
              .timeout(const Duration(seconds: 2));
        } catch (e) {
          await _teardownPlayer();
          try {
            await _ensureReady();
            await _player!
                .feedUint8FromStream(frame)
                .timeout(const Duration(seconds: 2));
          } catch (e2) {
            _queue.clear();
            _errors.add('playback failed: $e2');
            _turnActive = false;
            return;
          }
        }
      }
    } finally {
      _pumping = false;
    }
  }

  Future<void> _ensureReady() async {
    if (_playerReady && _player != null) return;
    final player = fs.FlutterSoundPlayer();
    _player = player;
    await player.openPlayer();
    await player.setSubscriptionDuration(const Duration(milliseconds: 50));
    await player.startPlayerFromStream(
      codec: fs.Codec.pcm16,
      numChannels: 1,
      sampleRate: sampleRate,
      bufferSize: bufferBytes,
      interleaved: true,
    );
    _playerReady = true;
  }

  Future<void> _teardownPlayer() async {
    final player = _player;
    _player = null;
    _playerReady = false;
    if (player == null) return;
    try {
      await player.stopPlayer().timeout(const Duration(seconds: 2));
    } catch (_) {}
    try {
      await player.closePlayer().timeout(const Duration(seconds: 2));
    } catch (_) {}
  }

  Future<void> endTurn() async {
    _turnActive = false;
    _queue.clear();
    await _teardownPlayer();
  }

  Future<void> abort() async {
    _turnActive = false;
    _queue.clear();
    await _teardownPlayer();
  }

  Future<void> dispose() async {
    _turnActive = false;
    _queue.clear();
    await _teardownPlayer();
    await _errors.close();
  }
}
