import 'dart:async';
import 'dart:typed_data';

import '../audio/mic_controller.dart';
import '../audio/speaker_controller.dart';
import '../connection/health_stats.dart';
import '../transport/robot_transport.dart';
import 'session_store.dart';
import 'turn_fsm.dart';
import 'voice_event.dart';

/// Orchestrates PTT turns on top of a connected [RobotTransport].
///
/// Fix for the "second PTT freezes" bug, beyond the audio-lifecycle rules:
/// - _pttBusy latch makes press/release idempotent and rejects overlapping
///   async transitions.
/// - starting a new turn force-interrupts any non-terminal previous turn.
/// - disconnect marks the active turn errored instead of leaving it hanging.
class SessionController {
  final RobotTransport Function() transport;
  final MicController mic;
  final SpeakerController speaker;
  final HealthStats stats;

  final _turnsCtrl = StreamController<ConversationTurn>.broadcast();
  final _stateCtrl = StreamController<String>.broadcast();

  ConversationTurn? activeTurn;
  TurnFsm? _fsm;
  bool _pttBusy = false;
  bool _micActive = false;
  StreamSubscription? _controlSub;
  StreamSubscription? _audioSub;
  StreamSubscription? _speakerErrSub;

  Stream<ConversationTurn> get turnUpdates => _turnsCtrl.stream;
  Stream<String> get audioState => _stateCtrl.stream;
  bool get micActive => _micActive;

  SessionController({
    required this.transport,
    required this.mic,
    required this.speaker,
    required this.stats,
  });

  void attach() {
    _controlSub = transport().control.listen(_onControl);
    _audioSub = transport().audio.listen(_onAudio);
    _speakerErrSub = speaker.errors.listen((e) {
      _stateCtrl.add('error');
      final turn = activeTurn;
      if (turn != null) {
        turn.state = 'error';
        turn.error = e;
        _turnsCtrl.add(turn);
      }
    });
  }

  void detach() {
    _controlSub?.cancel();
    _controlSub = null;
    _audioSub?.cancel();
    _audioSub = null;
    _speakerErrSub?.cancel();
    _speakerErrSub = null;
  }

  // ── PTT ──────────────────────────────────────────────────────────────
  Future<void> startTalking() async {
    if (_pttBusy || _micActive || !transport().connected) return;
    _pttBusy = true;
    try {
      // Force the previous turn terminal: the server keys a fresh voice
      // session off the first audio frame, and a turn still in flight would
      // silently swallow the new session's events.
      _interruptActive('被新一轮说话打断');

      final turn = ConversationTurn(id: DateTime.now().microsecondsSinceEpoch.toString());
      activeTurn = turn;
      _fsm = TurnFsm(onChanged: (_) => _turnsCtrl.add(turn));
      turn.state = 'recording';
      _turnsCtrl.add(turn);

      await speaker.beginTurn();

      await mic.start((pcm) {
        final t = transport();
        stats.txAudioBytes += pcm.length;
        t.sendAudio(pcm).catchError((Object e) {});
      });
      _micActive = true;
      _stateCtrl.add('recording');
      _fsm?.armWatchdog();
    } on MicException catch (e) {
      final turn = activeTurn;
      if (turn != null) {
        turn.state = 'error';
        turn.error = e.message;
        _turnsCtrl.add(turn);
      }
      _stateCtrl.add('error');
      activeTurn = null;
      _fsm?.dispose();
      _fsm = null;
    } finally {
      _pttBusy = false;
    }
  }

  Future<void> stopTalking() async {
    if (_pttBusy || !_micActive) return;
    _pttBusy = true;
    try {
      _micActive = false;
      await mic.stop();
      _stateCtrl.add('recognizing');
      final turn = activeTurn;
      if (turn != null && turn.state == 'recording') {
        turn.state = 'recognizing';
        _turnsCtrl.add(turn);
      }
      final t = transport();
      if (t.connected) {
        stats.onTxControl();
        await t.sendControl({'type': 'mic_end'}).catchError((Object e) {});
      }
      _fsm?.armWatchdog();
    } finally {
      _pttBusy = false;
    }
  }

  // ── inbound ─────────────────────────────────────────────────────────
  void _onAudio(Uint8List pcm) {
    stats.onRxAudio(pcm);
    speaker.feed(pcm);
  }

  void _onControl(Map<String, dynamic> value) {
    stats.onRxControl();
    final event = VoiceEvent.tryParse(value);
    if (event == null) return;
    final turn = activeTurn;
    if (turn == null) return;

    switch (event.kind) {
      case VoiceEventKind.asrFinal:
        if (event.text.isNotEmpty) turn.userText = event.text;
      case VoiceEventKind.pilot:
        if (event.text.isNotEmpty) turn.assistantText += event.text;
      case VoiceEventKind.error:
        turn.error = event.error.isNotEmpty ? event.error : event.status;
      default:
        break;
    }

    turn.state = switch (event.kind) {
      VoiceEventKind.sessionStarted ||
      VoiceEventKind.recordingStarted =>
        'recording',
      VoiceEventKind.recordingDone => 'recognizing',
      VoiceEventKind.asrFinal => 'thinking',
      VoiceEventKind.pilot => 'thinking',
      VoiceEventKind.ttsStarted || VoiceEventKind.ttsDone => 'playing',
      VoiceEventKind.sessionDone => 'done',
      VoiceEventKind.error => 'error',
      _ => turn.state,
    };
    _stateCtrl.add(turn.state);
    _turnsCtrl.add(turn);

    if (event.kind == VoiceEventKind.sessionDone ||
        event.kind == VoiceEventKind.error) {
      unawaited(_finishTurn());
    }
  }

  Future<void> _finishTurn() async {
    _fsm?.dispose();
    _fsm = null;
    activeTurn = null;
    await speaker.endTurn();
    _stateCtrl.add('idle');
  }

  void _interruptActive(String reason) {
    final turn = activeTurn;
    final fsm = _fsm;
    if (turn != null && !fsm!.isTerminal) {
      fsm.interrupt(reason);
      turn.state = 'error';
      turn.error = reason;
      _turnsCtrl.add(turn);
    }
    activeTurn = null;
    _fsm?.dispose();
    _fsm = null;
    unawaited(speaker.abort());
    _stateCtrl.add('idle');
  }

  /// Called by the connection layer when the link drops.
  Future<void> onDisconnected() async {
    _micActive = false;
    _interruptActive('连接已断开');
    await mic.stop();
    await speaker.abort();
    _stateCtrl.add('idle');
  }

  Future<void> dispose() async {
    detach();
    _interruptActive('控制器销毁');
    await mic.dispose();
    await speaker.dispose();
    await _turnsCtrl.close();
    await _stateCtrl.close();
  }
}
