import 'dart:async';
import 'dart:typed_data';

import '../audio/mic_controller.dart';
import '../audio/speaker_controller.dart';
import '../capture/capture_controller.dart';
import '../connection/health_stats.dart';
import '../transport/robot_transport.dart';
import 'pilot_text.dart';
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

  /// WS mode voice-session hooks (no-op on SPP, where the robot's SPP server
  /// drives Liaison). Wired from HomePage when the active transport is WS.
  final Future<void> Function(String sessionId, String historyJson)?
      onBeginVoiceSession;
  final Future<void> Function(String sessionId)? onEndVoiceCapture;
  /// Null return means "not WS mode — no client-driven session".
  final String? Function()? onNewSessionId;

  /// History provider for cross-turn context (set by HomePage).
  final String Function()? onHistoryJson;

  /// Offline capture (debug): writes the SPP-wire bytes that would be sent,
  /// independent of the link. When enabled, PTT is allowed while disconnected.
  final CaptureController? capture;
  /// Called with the capture folder path after a PTT capture is finalized.
  final void Function(String path)? onCaptureSaved;

  final _turnsCtrl = StreamController<ConversationTurn>.broadcast();
  final _stateCtrl = StreamController<String>.broadcast();

  ConversationTurn? activeTurn;
  TurnFsm? _fsm;
  bool _pttBusy = false;
  bool _micActive = false;
  String? _wsSessionId;
  StreamSubscription? _controlSub;
  StreamSubscription? _audioSub;
  StreamSubscription? _speakerErrSub;
  StreamSubscription? _micErrSub;

  Stream<ConversationTurn> get turnUpdates => _turnsCtrl.stream;
  Stream<String> get audioState => _stateCtrl.stream;
  bool get micActive => _micActive;

  SessionController({
    required this.transport,
    required this.mic,
    required this.speaker,
    required this.stats,
    this.onBeginVoiceSession,
    this.onEndVoiceCapture,
    this.onNewSessionId,
    this.onHistoryJson,
    this.capture,
    this.onCaptureSaved,
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
    _micErrSub = mic.errors.listen((e) {
      _stateCtrl.add('error');
      final turn = activeTurn;
      if (turn != null) {
        turn.state = 'error';
        turn.error = e.message;
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
    _micErrSub?.cancel();
    _micErrSub = null;
  }

  // ── PTT ──────────────────────────────────────────────────────────────
  Future<void> startTalking() async {
    // 未连接时仅在离线捕获开启时放行，让"录音→发送前"整条链路可脱离 SPP 调试。
    final captureEnabled = capture?.enabled ?? false;
    if (_pttBusy || _micActive || (!transport().connected && !captureEnabled)) return;
    _pttBusy = true;
    final offlineCapture = !transport().connected;
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

      // 捕获挂在 sendAudio 之前，记录"SPP 传输前"的同一份字节。
      final cap = await capture?.begin(
        sessionId: turn.id,
        mode: transport().mode.name,
      );

      // WS mode: start the Liaison voice session BEFORE audio flows (the
      // bridge drops PCM until a mic stream exists). Only when the link is
      // actually up — offline capture never reaches Liaison.
      if (transport().connected && onNewSessionId != null) {
        _wsSessionId = onNewSessionId!();
        await onBeginVoiceSession?.call(_wsSessionId!, onHistoryJson?.call() ?? '');
      }

      await mic.start((pcm) {
        cap?.onPcm(pcm);
        final t = transport();
        stats.txAudioBytes += pcm.length;
        t.sendAudio(pcm).catchError((Object e) {});
      });
      _micActive = true;
      _stateCtrl.add('recording');
      if (!offlineCapture) _fsm?.armWatchdog();
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

      // 松开必写 mic_end 帧 + meta 落盘，与连接状态无关。
      final capturedPath = await capture?.endActive();
      if (capturedPath != null) onCaptureSaved?.call(capturedPath);

      final t = transport();
      if (t.connected) {
        stats.onTxControl();
        await t.sendControl({'type': 'mic_end'}).catchError((Object e) {});
        // WS mode: finalize ASR early instead of waiting out record_seconds.
        final sessionId = _wsSessionId;
        if (sessionId != null) {
          await onEndVoiceCapture?.call(sessionId);
        }
        _fsm?.armWatchdog();
      } else {
        // 离线捕获回合（或连接中途断开）：立即收尾，不挂 watchdog、不等回包。
        final offlineCapture = capture?.enabled ?? false;
        if (turn != null && offlineCapture && !_fsm!.isTerminal) {
          turn.state = 'done';
          _turnsCtrl.add(turn);
        }
        await _finishTurn();
      }
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
    // 刷新 60s 软看门狗：分阶段回答（先述→调相机→再描述）间隔可能超过 60s，
    // 但引擎在会话结束前会持续推送事件——不刷新就会把长回合误判成"无响应超时"。
    _fsm?.armWatchdog();

    switch (event.kind) {
      case VoiceEventKind.asrPartial:
        // ASR 中间结果即时回显，不等 asrFinal
        if (event.text.isNotEmpty) turn.userText = event.text;
      case VoiceEventKind.asrFinal:
        if (event.text.isNotEmpty) turn.userText = event.text;
      case VoiceEventKind.pilot:
        // Pilot 长回复会先流式发 text_chunk，收尾再发 final_text（全文）。
        // 逐条累加会把全文重复一遍，需用官方客户端 mergeFinalText 语义去重。
        turn.assistantText = mergePilotText(turn.assistantText, event.text);
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
    await capture?.endActive();
    await speaker.abort();
    _stateCtrl.add('idle');
  }

  Future<void> dispose() async {
    detach();
    _interruptActive('控制器销毁');
    await mic.dispose();
    await speaker.dispose();
    await capture?.dispose();
    await _turnsCtrl.close();
    await _stateCtrl.close();
  }
}
