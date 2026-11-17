import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';

/// Owns the camera across permission dialogs, backgrounding and resume.
/// A suspended controller is detached immediately, before disposal can await.
class NavigationCameraSession {
  NavigationCameraSession(this.createController);
  final Future<CameraController> Function() createController;
  CameraController? controller;
  Future<CameraController?>? _initializing;
  Future<void> _disposing = Future.value();
  int generation = 0;
  bool _active = true;
  bool _closed = false;

  Future<CameraController?> resume() async {
    if (_closed) return null;
    _active = true;
    while (_active && !_closed) {
      if (controller != null) return controller;
      final pending = _initializing;
      if (pending != null) {
        await pending;
        continue;
      }
      final operation = _initialize(generation);
      _initializing = operation;
      try {
        final result = await operation;
        if (result != null) return result;
      } finally {
        if (identical(_initializing, operation)) _initializing = null;
      }
    }
    return null;
  }

  Future<CameraController?> _initialize(int expectedGeneration) async {
    await _disposing;
    if (!_active || _closed || generation != expectedGeneration) return null;
    final candidate = await createController();
    try {
      if (_active && !_closed && generation == expectedGeneration) {
        await candidate.initialize();
      }
      if (!_active || _closed || generation != expectedGeneration) {
        await _release(candidate);
        return null;
      }
      controller = candidate;
      return candidate;
    } catch (_) {
      await _release(candidate);
      rethrow;
    }
  }

  Future<void> suspend() {
    _active = false;
    generation++;
    final previous = controller;
    controller = null;
    if (previous != null) {
      _disposing = _disposing.then((_) => _release(previous));
    }
    return _disposing;
  }

  Future<void> close() {
    _closed = true;
    return suspend();
  }

  Future<void> _release(CameraController camera) async {
    try {
      await camera.dispose();
    } catch (error) {
      // A platform cleanup failure must not poison all subsequent resumes.
      debugPrint('Navigation camera cleanup failed: $error');
    }
  }
}
