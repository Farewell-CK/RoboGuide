import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Recompress the full camera view on Android's worker thread. Keep original
/// pixels on unsupported devices or when JPEG recompression saves no bandwidth.
Future<Uint8List> prepareNavigationUpload(Uint8List original) async {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android)
    return original;
  try {
    final packed = await const MethodChannel(
      'navi/navigation_image',
    ).invokeMethod<Uint8List>('compress', original);
    if (packed != null &&
        packed.isNotEmpty &&
        packed.length < original.length) {
      return packed;
    }
  } on MissingPluginException {
    // Other builds can keep using the original request format.
  } on PlatformException {
    // An optional transport optimization must not drop a usable camera view.
  }
  return original;
}
