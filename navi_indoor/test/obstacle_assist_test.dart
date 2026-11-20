import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/services/obstacle_monitor.dart';
import 'package:indoor_navigation_app/services/navigation_prompts.dart';
import 'package:indoor_navigation_app/widgets/obstacle_assist_panel.dart';
import 'package:indoor_navigation_app/widgets/navigation_prompt_panel.dart';

const urgentPerson = ObstacleDetection(
  classId: 12,
  label: '行人',
  position: '前方',
  level: 'caution',
  confidence: .95,
  occupancy: .12,
  box: [.35, .4, .65, .95],
);
ObstacleObservation personObservation(String id) => ObstacleObservation(
  id,
  urgentPerson.message,
  'caution',
  detections: const [urgentPerson],
);

void main() {
  final bytes = Uint8List.fromList([1, 2, 3]);
  test('disabled assistance sends no images', () async {
    var calls = 0;
    final monitor = ObstacleMonitor(
      analyze: (_, id) async {
        calls++;
        return personObservation(id);
      },
      onWarning: (_) {},
      onClear: () {},
      automaticExpiry: false,
    );
    monitor.setActive(true);
    await monitor.submit(bytes, DateTime.now());
    expect(calls, 0);
    monitor.dispose();
  });
  test(
    'in-flight images are dropped and a paused session cannot announce',
    () async {
      final pending = Completer<ObstacleObservation>();
      var calls = 0;
      var frame = '';
      final warnings = <String>[];
      final monitor = ObstacleMonitor(
        analyze: (_, id) {
          calls++;
          frame = id;
          return pending.future;
        },
        onWarning: warnings.add,
        onClear: () {},
        automaticExpiry: false,
      );
      monitor.setEnabled(true);
      monitor.setActive(true);
      final first = monitor.submit(bytes, DateTime.now());
      await monitor.submit(bytes, DateTime.now());
      expect(calls, 1);
      monitor.setActive(false);
      monitor.setActive(true);
      pending.complete(personObservation(frame));
      await first;
      expect(warnings, isEmpty);
      expect(monitor.warning, isFalse);
      monitor.dispose();
    },
  );
  test(
    'capture-time age rejects slow responses and expires displayed warnings',
    () async {
      var now = DateTime(2026);
      final warnings = <String>[];
      var slow = true;
      final monitor = ObstacleMonitor(
        analyze: (_, id) async {
          if (slow) now = now.add(const Duration(seconds: 4));
          return personObservation(id);
        },
        clock: () => now,
        onWarning: warnings.add,
        onClear: () {},
        automaticExpiry: false,
      );
      monitor.setEnabled(true);
      monitor.setActive(true);
      await monitor.submit(bytes, now);
      expect(warnings, isEmpty);
      expect(monitor.status, contains('过期'));
      slow = false;
      await monitor.submit(bytes, now);
      expect(warnings, hasLength(1));
      now = now.add(const Duration(seconds: 4));
      monitor.expire();
      expect(monitor.warning, isFalse);
      expect(monitor.status, contains('过期'));
      monitor.dispose();
    },
  );
  test(
    'disconnect replaces previous warning with unavailable, not clear passage',
    () async {
      var fail = false;
      var now = DateTime(2026);
      final monitor = ObstacleMonitor(
        analyze: (_, id) async {
          if (fail) throw StateError('network');
          return personObservation(id);
        },
        clock: () => now,
        onWarning: (_) {},
        onClear: () {},
        automaticExpiry: false,
      );
      monitor.setEnabled(true);
      monitor.setActive(true);
      await monitor.submit(bytes, now);
      expect(monitor.warning, isTrue);
      fail = true;
      now = now.add(const Duration(seconds: 1));
      await monitor.submit(bytes, now);
      expect(monitor.warning, isFalse);
      expect(monitor.status, contains('暂不可用'));
      monitor.dispose();
    },
  );
  test('a mismatched frame ID cannot produce a warning', () async {
    final monitor = ObstacleMonitor(
      analyze:
          (_, id) async =>
              const ObstacleObservation('old-frame', '前方行人', 'caution'),
      onWarning: (_) => fail('Unexpected warning'),
      onClear: () {},
      automaticExpiry: false,
    );
    monitor.setEnabled(true);
    monitor.setActive(true);
    await monitor.submit(bytes, DateTime.now());
    expect(monitor.warning, isFalse);
    monitor.dispose();
  });
  test('unexpected metric-depth or missing capability fields are rejected', () {
    expect(
      () => ObstacleObservation.fromJson({
        'frame_id': '1',
        'level': 'caution',
        'message': 'test',
        'distance_available': true,
        'automatic_detour': false,
      }),
      throwsFormatException,
    );
  });
  testWidgets('obstacle audio yields to arrival and restores pending turn', (
    tester,
  ) async {
    final spoken = <String>[];
    final prompts = NavigationPrompts(
      configure: () async {},
      stop: () async => 1,
      speak: (message) async {
        spoken.add(message);
        return 1;
      },
    );
    prompts.show('继续直行');
    await tester.pump();
    prompts.showObstacle('前方行人，请停下观察。');
    prompts.show('前方左转', kind: NavigationPromptKind.turn);
    await tester.pump();
    expect(prompts.text, contains('行人'));
    prompts.clearObstacle();
    await tester.pump();
    expect(prompts.text, '前方左转');
    expect(spoken.last, '前方左转');
    prompts.showObstacle('前方行人，请停下观察。');
    prompts.show('已到达', kind: NavigationPromptKind.arrival);
    prompts.clearObstacle();
    await tester.pump();
    expect(prompts.text, '已到达');
    expect(spoken.last, '已到达');
    prompts.dispose();
    await tester.pump();
  });
  testWidgets(
    'small screen with enlarged text retains stop action and obstacle switch',
    (tester) async {
      tester.view.physicalSize = const Size(320, 568);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final monitor = ObstacleMonitor(
        analyze: (_, id) async => ObstacleObservation(id, '', 'unknown'),
        onWarning: (_) {},
        onClear: () {},
        automaticExpiry: false,
      );
      final prompts = NavigationPrompts(
        configure: () async {},
        stop: () async => 1,
        speak: (_) async => 1,
      );
      await tester.pumpWidget(
        MaterialApp(
          builder:
              (context, child) => MediaQuery(
                data: MediaQuery.of(
                  context,
                ).copyWith(textScaler: const TextScaler.linear(2)),
                child: child!,
              ),
          home: Scaffold(
            body: Align(
              alignment: Alignment.bottomCenter,
              child: NavigationPromptLayout(
                prompts: prompts,
                assist: ObstacleAssistPanel(monitor: monitor),
                details: const Text('路线跟踪 12 / 27'),
                actions: TextButton(
                  onPressed: () {},
                  child: const Text('停止导航'),
                ),
              ),
            ),
          ),
        ),
      );
      expect(tester.takeException(), isNull);
      expect(find.byType(Switch), findsOneWidget);
      await tester.tap(find.byType(Switch));
      await tester.pump();
      expect(monitor.enabled, isTrue);
      expect(find.text('停止导航'), findsOneWidget);
      monitor.dispose();
      prompts.dispose();
      await tester.pump();
    },
  );
}
