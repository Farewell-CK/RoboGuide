import 'dart:async';
import 'dart:collection';
import 'dart:typed_data';

import 'package:flutter_sound/flutter_sound.dart' as fs;

/// Speaker playback with per-turn player lifecycle. Root cause of the
/// "second PTT freezes" bug lived here: a single FlutterSoundPlayer was
/// reused across turns; flutter_sound 9.x throws "already initialized" on a
/// second openPlayer() and can hang startPlayerFromStream on a stale
/// instance, and the old serial `.then` chain stopped forever once one job
/// hung. Rules now:
/// - one player per turn: open -> startPlayerFromStream -> feed* -> close;
///   beginTurn() releases the previous player first.
/// - finite queue + single-flight pump, never an unbounded .then chain.
/// - every feed is time-boxed; on timeout the player is rebuilt.
class SpeakerController {
  static const int sampleRate = 16000;
  static const int channels = 1;
  static const int frameBytes = 1600;

  fs.FlutterSoundPlayer? _player;
  bool _playerReady = false;
  final Queue<Uint8List> _queue = Queue<Uint8List>();
  bool _pumping = false;
  bool _turnActive = false;

  /// Playback errors surfaced to the session layer (turn should go error).
  final _errors = StreamController<String>.broadcast();
  Stream<String> get errors => _errors.stream;

  /// Begin a turn: any previous player is torn down (it belonged to the
  /// previous turn), state resets.
  Future<void> beginTurn() async {
    _turnActive = true;
    await _teardownPlayer();
    _queue.clear();
  }

  /// Enqueue PCM for playback. Returns immediately; a single-flight pump
  /// drains the queue.
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
        try {
          await _ensureReady();
          await _player!.feedUint8FromStream(frame)
              .timeout(const Duration(seconds: 1));
        } catch (e) {
          // wedged player: rebuild and retry this frame once
          await _teardownPlayer();
          try {
            await _ensureReady();
            await _player!.feedUint8FromStream(frame)
                .timeout(const Duration(seconds: 1));
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
    await player.startPlayerFromStream(
      codec: fs.Codec.pcm16,
      interleaved: true,
      numChannels: channels,
      sampleRate: sampleRate,
      bufferSize: frameBytes,
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

  /// End the turn: drain what's queued, then release the player.
  Future<void> endTurn() async {
    // small grace so the tail of the audio can be pumped
    final deadline = DateTime.now().add(const Duration(milliseconds: 300));
    while (_queue.isNotEmpty && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
    _turnActive = false;
    _queue.clear();
    await _teardownPlayer();
  }

  /// Immediate stop (disconnect/error paths).
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
