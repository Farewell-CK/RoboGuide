import 'dart:async';
import 'package:flutter/foundation.dart';

/// Optional speech must never hold up camera, localization or navigation.
void speakWithoutBlocking(Future<dynamic> Function() speak) {
  unawaited(
    Future<void>(() async {
      try {
        await speak().timeout(const Duration(seconds: 2));
      } catch (error) {
        debugPrint('语音播报暂不可用：$error');
      }
    }),
  );
}
