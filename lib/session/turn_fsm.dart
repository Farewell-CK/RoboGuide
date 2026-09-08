import 'dart:async';

import 'voice_event.dart';

/// Turn lifecycle states.
enum TurnState { recording, recognizing, thinking, playing, done, error }

/// Maps an official VoiceEventKind onto a turn state transition. Pure logic,
/// unit-testable without Flutter.
class TurnFsm {
  TurnState state = TurnState.recording;
  String userText = '';
  String assistantText = '';
  String error = '';

  Timer? _watchdog;
  final Duration softTimeout;
  final void Function(TurnFsm fsm) onChanged;

  TurnFsm({
    this.softTimeout = const Duration(seconds: 20),
    required this.onChanged,
  });

  bool get isTerminal => state == TurnState.done || state == TurnState.error;

  /// Apply a voice event; returns true when the event was consumed.
  bool apply(VoiceEvent event) {
    switch (event.kind) {
      case VoiceEventKind.sessionStarted:
      case VoiceEventKind.recordingStarted:
        _set(TurnState.recording);
      case VoiceEventKind.recordingDone:
        _set(TurnState.recognizing);
      case VoiceEventKind.asrPartial:
        break;
      case VoiceEventKind.asrFinal:
        if (event.text.isNotEmpty) userText = event.text;
        _set(TurnState.thinking);
      case VoiceEventKind.userIdentified:
        break;
      case VoiceEventKind.pilot:
        if (event.text.isNotEmpty) assistantText += event.text;
        _set(TurnState.thinking);
      case VoiceEventKind.ttsStarted:
      case VoiceEventKind.ttsDone:
        _set(TurnState.playing);
      case VoiceEventKind.sessionDone:
        _set(TurnState.done);
        _cancelWatchdog();
      case VoiceEventKind.error:
        error = event.error.isNotEmpty ? event.error : event.status;
        _set(TurnState.error);
        _cancelWatchdog();
      case VoiceEventKind.unknown:
        return false;
    }
    return true;
  }

  /// A turn that never receives another event must not hang forever: a soft
  /// timeout fires unless events keep arriving (each event re-arms it).
  void armWatchdog() {
    _cancelWatchdog();
    _watchdog = Timer(softTimeout, () {
      if (isTerminal) return;
      error = '语音会话无响应（超时），请重试';
      _set(TurnState.error);
    });
  }

  void _cancelWatchdog() {
    _watchdog?.cancel();
    _watchdog = null;
  }

  /// Force a non-terminal turn into a terminal state (new PTT arriving or
  /// disconnect while an old turn was still in flight).
  void interrupt(String reason) {
    if (isTerminal) return;
    error = reason;
    _set(TurnState.error);
    _cancelWatchdog();
  }

  void _set(TurnState next) {
    state = next;
    if (!isTerminal) armWatchdog();
    onChanged(this);
  }

  void dispose() {
    _cancelWatchdog();
  }
}
