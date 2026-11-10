import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:indoor_navigation_app/services/dinov2_service.dart';
import 'package:indoor_navigation_app/services/navigation_feedback.dart';
import 'package:indoor_navigation_app/widgets/localization_ready_panel.dart';
import 'package:indoor_navigation_app/widgets/navigation_content_frame.dart';

class TestCamera extends CameraController {
  TestCamera(this.frame)
    : super(
        const CameraDescription(
          name: 'test',
          lensDirection: CameraLensDirection.back,
          sensorOrientation: 0,
        ),
        ResolutionPreset.medium,
        enableAudio: false,
      ) {
    value = value.copyWith(isInitialized: true);
  }
  final File frame;
  @override
  Future<XFile> takePicture() async => XFile(frame.path);
}

class DisposedTestCamera extends TestCamera {
  DisposedTestCamera() : super(File('unused.jpg'));

  @override
  Future<XFile> takePicture() async =>
      throw CameraException(
        'Disposed CameraController',
        'takePicture() was called on a disposed CameraController.',
      );
}

void main() {
  test(
    'a disposed camera fails before HTTP with a camera recovery message',
    () async {
      var requests = 0;
      final service = ClipService(
        serverUrl: 'http://example.test',
        embeddingClient: MockClient((_) async {
          requests++;
          return http.Response('{}', 500);
        }),
      );
      final result = await service.performEnhancedLocalization(
        cameraController: DisposedTestCamera(),
        onStatusUpdate: (_) {},
        disableYolo: true,
      );
      expect(result.success, isFalse);
      expect(result.capturedFrameCount, 0);
      expect(result.errorMessage, contains('相机拍照已中断'));
      expect(result.errorMessage, isNot(contains('换一个位置')));
      expect(requests, 0);
    },
  );
  test(
    'navigation connection failures are errors rather than zero-vector observations',
    () async {
      final temp = await Directory.systemTemp.createTemp('navi-nav-error-');
      addTearDown(() => temp.delete(recursive: true));
      final frame = await File(
        '${temp.path}/frame.jpg',
      ).writeAsBytes([1, 2, 3]);
      final service = ClipService(
        serverUrl: 'http://example.test',
        embeddingClient: MockClient(
          (_) async => throw const SocketException('offline'),
        ),
      );
      await expectLater(
        service.generateNavigationEmbeddingWithInpainting(
          frame,
          cleanSceneThreshold: .7,
          peoplePresentThreshold: .7,
          crowdedSceneThreshold: .7,
          disableYolo: true,
        ),
        throwsException,
      );
    },
  );
  testWidgets(
    'Chinese controls stay tappable with a small screen and large text; speech never blocks start',
    (tester) async {
      tester.view.physicalSize = const Size(320, 568);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var people = false;
      var started = false;
      final neverFinishes = Completer<void>();
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: const MediaQueryData(
              size: Size(320, 568),
              textScaler: TextScaler.linear(2),
            ),
            child: Scaffold(
              body: Align(
                alignment: Alignment.bottomCenter,
                child: StatefulBuilder(
                  builder:
                      (context, setState) => Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          NavigationContentFrame(
                            child: LocalizationReadyPanel(
                              status: '请站在已录制的节点附近，点击“开始定位”',
                              verifyWithGpt: false,
                              peopleInpainting: people,
                              onVerificationChanged: null,
                              onPeopleChanged:
                                  (v) => setState(() => people = v),
                            ),
                          ),
                          ElevatedButton(
                            onPressed: () {
                              speakWithoutBlocking(() => neverFinishes.future);
                              setState(() => started = true);
                            },
                            child: const Text('开始定位'),
                          ),
                          if (started) const Text('正在定位'),
                        ],
                      ),
                ),
              ),
            ),
          ),
        ),
      );
      final peopleTile = find.widgetWithText(SwitchListTile, '行人消除');
      await tester.ensureVisible(peopleTile);
      await tester.tap(peopleTile);
      await tester.pump();
      expect(people, isTrue);
      await tester.tap(find.text('开始定位'));
      await tester.pump();
      expect(find.text('正在定位'), findsOneWidget);
      expect(neverFinishes.isCompleted, isFalse);
      expect(tester.takeException(), isNull);
      await tester.pump(const Duration(seconds: 3));
      neverFinishes.complete();
      await tester.pump();
    },
  );

  test(
    'cancelled scan uses raw images when inpainting is off and cleans temporary images',
    () async {
      final temp = await Directory.systemTemp.createTemp('navi-scan-');
      addTearDown(() => temp.delete(recursive: true));
      final frame = await File(
        '${temp.path}/frame.jpg',
      ).writeAsBytes([1, 2, 3]);
      var cancelled = false;
      final service = ClipService(
        serverUrl: 'http://example.test',
        embeddingClient: MockClient((request) async {
          expect(request.url.queryParameters['disable_yolo'], 'true');
          cancelled = true;
          return http.Response(
            jsonEncode({
              'embedding': [1, ...List.filled(767, 0)],
            }),
            200,
          );
        }),
      );
      final result = await service.performEnhancedLocalization(
        cameraController: TestCamera(frame),
        onStatusUpdate: (_) {},
        disableYolo: true,
        isCancelled: () => cancelled,
      );
      expect(result.success, isFalse);
      expect(result.errorMessage, contains('定位已取消'));
      expect(await frame.exists(), isFalse);
    },
  );

  test(
    'a model connection error ends the scan with a visible error instead of a fake location',
    () async {
      final temp = await Directory.systemTemp.createTemp('navi-scan-');
      addTearDown(() => temp.delete(recursive: true));
      final frame = await File(
        '${temp.path}/frame.jpg',
      ).writeAsBytes([1, 2, 3]);
      final service = ClipService(
        serverUrl: 'http://example.test',
        embeddingClient: MockClient(
          (_) async => throw const SocketException('offline'),
        ),
      );
      final status = <String>[];
      final result = await service.performEnhancedLocalization(
        cameraController: TestCamera(frame),
        onStatusUpdate: status.add,
        disableYolo: true,
      );
      expect(status.first, contains('正在扫描'));
      expect(result.success, isFalse);
      expect(result.errorMessage, contains('图片特征提取失败'));
      expect(await frame.exists(), isFalse);
    },
  );
}
