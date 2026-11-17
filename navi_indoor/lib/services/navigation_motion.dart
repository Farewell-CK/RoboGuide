import 'dart:io';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

/// Step events establish movement, not position or distance.
class NavigationMotion {
  static const _channel = MethodChannel('navi/navigation_motion');
  int _generation = 0;
  bool available = false;
  int steps = 0;

  Future<void> start() async {
    final token = ++_generation;
    available = false;
    steps = 0;
    if (!Platform.isAndroid) return;
    try {
      final permission = await Permission.activityRecognition.request();
      if (token != _generation || !permission.isGranted) return;
      final ready =
          await _channel.invokeMethod<bool>('start', {
            'token': '${identityHashCode(this)}:$token',
          }) ??
          false;
      if (token != _generation) {
        await _channel.invokeMethod<void>('stop', {
          'token': '${identityHashCode(this)}:$token',
        });
        return;
      }
      available = ready;
    } on PlatformException {
      if (token == _generation) available = false;
    } on MissingPluginException {
      if (token == _generation) available = false;
    }
  }

  Future<void> refresh() async {
    if (!available) return;
    final token = _generation;
    try {
      final count = await _channel.invokeMethod<int>('steps', {
        'token': '${identityHashCode(this)}:$token',
      });
      if (token != _generation) return;
      if (count == null) {
        available = false;
        return;
      }
      if (count >= steps) steps = count;
    } on PlatformException {
      if (token == _generation) available = false;
    } on MissingPluginException {
      if (token == _generation) available = false;
    }
  }

  Future<void> stop() async {
    final token = _generation++;
    available = false;
    steps = 0;
    if (!Platform.isAndroid) return;
    try {
      await _channel.invokeMethod<void>('stop', {
        'token': '${identityHashCode(this)}:$token',
      });
    } on PlatformException {
      /* Token invalidates pending reads. */
    } on MissingPluginException {
      /* No native sensor on this platform. */
    }
  }
}
