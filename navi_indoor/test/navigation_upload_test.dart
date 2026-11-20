import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:indoor_navigation_app/services/dinov2_service.dart';
import 'package:indoor_navigation_app/services/navigation_upload.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('navi/navigation_image');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  setUp(() {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
  });
  tearDown(() {
    messenger.setMockMethodCallHandler(channel, null);
    debugDefaultTargetPlatformOverride = null;
  });

  test(
    'unavailable or larger compression preserves the original camera view',
    () async {
      final bytes = Uint8List.fromList([1, 2, 3]);
      expect(await prepareNavigationUpload(bytes), bytes);
      for (final output in [Uint8List(0), Uint8List(10)]) {
        messenger.setMockMethodCallHandler(channel, (_) async => output);
        expect(await prepareNavigationUpload(bytes), bytes);
      }
      messenger.setMockMethodCallHandler(channel, (_) async {
        throw PlatformException(code: 'image_compression');
      });
      expect(await prepareNavigationUpload(bytes), bytes);
    },
  );

  test(
    'only navigation sends compact bytes; recording stays unchanged and request timings are measured',
    () async {
      final temp = await Directory.systemTemp.createTemp('navi-upload-');
      final original = Uint8List.fromList(List.filled(100, 11));
      final packed = Uint8List.fromList([255, 216, 1, 255, 217]);
      final file = await File('${temp.path}/frame.jpg').writeAsBytes(original);
      var compressions = 0;
      messenger.setMockMethodCallHandler(channel, (call) async {
        expect(call.method, 'compress');
        expect(call.arguments, original);
        compressions++;
        return packed;
      });
      final service = ClipService(
        serverUrl: 'http://example.test',
        embeddingClient: MockClient((request) async {
          final expected =
              request.url.path == '/encode/navigation' ? packed : original;
          expect(request.bodyBytes, containsAllInOrder(expected));
          return http.Response(
            jsonEncode({
              'embedding': [1, ...List.filled(767, 0)],
            }),
            200,
          );
        }),
      );
      try {
        await service.generateNavigationEmbedding(file);
        expect(compressions, 1);
        expect(service.lastEmbeddingTiming!['original_bytes'], 100);
        expect(service.lastEmbeddingTiming!['upload_bytes'], packed.length);
        expect(service.lastEmbeddingTiming!['request_ms'], isNonNegative);
        await service.generatePreprocessedEmbedding(file);
        expect(compressions, 1);
        expect(service.lastEmbeddingTiming!['upload_bytes'], 100);
      } finally {
        await temp.delete(recursive: true);
      }
    },
  );
}
