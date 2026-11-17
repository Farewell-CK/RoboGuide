import 'dart:async';
import 'package:flutter/foundation.dart';

enum NavigationPromptKind {
  progress,
  orientation,
  instruction,
  turn,
  warning,
  obstacle,
  arrival,
}

/// Subtitles update synchronously. Audio is optional and never blocks navigation.
class NavigationPrompts extends ChangeNotifier {
  NavigationPrompts({
    required this.speak,
    required this.stop,
    required this.configure,
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now {
    _ready = _configureSpeech();
  }

  final Future<dynamic> Function(String) speak;
  final Future<dynamic> Function() stop;
  final Future<void> Function() configure;
  final DateTime Function() _clock;
  late Future<void> _ready;
  bool _speechConfigured = false;
  bool _configurationFailed = false;
  String text = '请站在已录制的节点附近，点击开始定位。';
  NavigationPromptKind kind = NavigationPromptKind.progress;
  bool voiceEnabled = true;
  String? voiceError;
  bool _closed = false;
  bool _active = true;
  bool _draining = false;
  String? _queued;
  int _revision = 0;
  DateTime? _turnUntil;
  Timer? _obstacleExpiry;
  String? _deferredText;
  NavigationPromptKind? _deferredKind;
  bool _deferredAnnounce = false;

  void showObstacle(String message) {
    if (_closed || !_active || kind == NavigationPromptKind.arrival) return;
    if (kind != NavigationPromptKind.obstacle) {
      _deferredText = text;
      _deferredKind = kind;
      _deferredAnnounce = false;
    }
    show(
      message,
      kind: NavigationPromptKind.obstacle,
      key: 'obstacle:$message',
    );
    _obstacleExpiry?.cancel();
    _obstacleExpiry = Timer(const Duration(seconds: 3), clearObstacle);
  }

  void clearObstacle() {
    _obstacleExpiry?.cancel();
    if (!_closed && kind == NavigationPromptKind.obstacle) {
      final message = _deferredText;
      final announce = _deferredAnnounce;
      kind = _deferredKind ?? NavigationPromptKind.instruction;
      _deferredText = null;
      _deferredKind = null;
      if (message != null) show(message, kind: kind, announce: announce);
    }
  }

  final Map<String, DateTime> _spokenAt = {};

  void show(
    String message, {
    NavigationPromptKind kind = NavigationPromptKind.instruction,
    bool announce = true,
    String? key,
  }) {
    if (_closed || message.trim().isEmpty) return;
    if (this.kind == NavigationPromptKind.arrival &&
        kind != NavigationPromptKind.arrival)
      return;
    if (this.kind == NavigationPromptKind.obstacle &&
        kind != NavigationPromptKind.obstacle &&
        kind != NavigationPromptKind.arrival &&
        kind != NavigationPromptKind.warning) {
      _deferredText = message;
      _deferredKind = kind;
      _deferredAnnounce = announce;
      return;
    }
    final now = _clock();
    if (this.kind == NavigationPromptKind.turn &&
        _turnUntil != null &&
        now.isBefore(_turnUntil!) &&
        (kind == NavigationPromptKind.instruction ||
            kind == NavigationPromptKind.progress))
      return;
    if (kind == NavigationPromptKind.turn &&
        (text != message || this.kind != kind)) {
      _turnUntil = now.add(const Duration(seconds: 4));
    }
    if (text != message || this.kind != kind) {
      text = message;
      this.kind = kind;
      notifyListeners();
    }
    final id = key ?? message;
    final previous = _spokenAt[id];
    if (!announce ||
        !voiceEnabled ||
        !_active ||
        (previous != null &&
            now.difference(previous) < const Duration(seconds: 15)))
      return;
    _spokenAt[id] = now;
    _queue(message);
  }

  void reset() {
    if (_closed) return;
    _revision++;
    _queued = null;
    _spokenAt.clear();
    _obstacleExpiry?.cancel();
    _deferredText = null;
    _deferredKind = null;
    _turnUntil = null;
    kind = NavigationPromptKind.progress;
    text = '请站在已录制的节点附近，点击开始定位。';
    _stopSafely();
    notifyListeners();
  }

  void toggleVoice() {
    if (_closed) return;
    voiceEnabled = !voiceEnabled;
    if (!voiceEnabled) {
      _revision++;
      _queued = null;
      _stopSafely();
    }
    notifyListeners();
    if (voiceEnabled) repeat();
  }

  void repeat() {
    if (!_closed && voiceEnabled && _active) {
      if (_configurationFailed) {
        _configurationFailed = false;
        _ready = _configureSpeech();
      }
      _queue(text);
    }
  }

  void suspend() {
    clearObstacle();
    _active = false;
    _revision++;
    _queued = null;
    _stopSafely();
  }

  void resume() => _active = true;

  void reportVoiceError() {
    if (_closed) return;
    voiceError = '语音暂不可用，请查看字幕；可检查手机中文语音引擎和媒体音量。';
    notifyListeners();
  }

  void _queue(String message) {
    _queued = message;
    _revision++;
    if (!_draining) unawaited(_drain());
  }

  Future<void> _configureSpeech() async {
    try {
      await Future<void>.sync(configure).timeout(const Duration(seconds: 6));
      _speechConfigured = true;
    } catch (_) {
      _configurationFailed = true;
      reportVoiceError();
    }
  }

  Future<void> _drain() async {
    _draining = true;
    try {
      await _ready;
      if (!_speechConfigured) {
        _queued = null;
        return;
      }
      while (!_closed && _active && voiceEnabled && _queued != null) {
        final message = _queued!;
        final revision = _revision;
        _queued = null;
        await _stopSafely();
        if (_closed || !_active || !voiceEnabled || revision != _revision)
          continue;
        try {
          final result = await speak(
            message,
          ).timeout(const Duration(seconds: 2));
          if (result == 0 || result == false) {
            reportVoiceError();
          } else if (!_closed && voiceError != null) {
            voiceError = null;
            notifyListeners();
          }
        } catch (_) {
          reportVoiceError();
        }
      }
    } finally {
      _draining = false;
    }
  }

  Future<void> _stopSafely() async {
    try {
      await stop().timeout(const Duration(milliseconds: 500));
    } catch (_) {}
  }

  @override
  void dispose() {
    _obstacleExpiry?.cancel();
    _closed = true;
    suspend();
    super.dispose();
  }
}
