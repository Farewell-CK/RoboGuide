import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/services/obstacle_monitor.dart';
import 'package:indoor_navigation_app/widgets/obstacle_assist_panel.dart';

ObstacleDetection chair({
  List<double> box = const [.4, .4, .6, .9],
  double occupancy = .08,
  String label = '椅子',
  int id = 19,
}) => ObstacleDetection(
  classId: id,
  label: label,
  position: '前方',
  level: 'caution',
  confidence: .9,
  occupancy: occupancy,
  box: box,
);

Map<String, dynamic> wire() => {
  'frame_id': '1',
  'policy_version': 2,
  'profile': 'indoor',
  'message': '前方识别到椅子，请停下观察。',
  'level': 'caution',
  'distance_available': false,
  'automatic_detour': false,
  'inference_ms': 85,
  'detections': [
    {
      'class_id': 19,
      'label': '椅子',
      'position': '前方',
      'level': 'caution',
      'confidence': .9,
      'occupancy': .08,
      'box': [.4, .4, .6, .9],
      'distance_m': null,
    },
  ],
  'raw_predictions': [
    {
      'class_id': 87,
      'model_label': 'streetlight',
      'confidence': .95,
      'occupancy': .03,
      'disposition': 'filtered_scene',
    },
  ],
};

void main() {
  final start = DateTime(2026, 11, 16);
  test('ordinary object needs two spatially consistent frames', () {
    final confirmation = ObstacleConfirmation();
    expect(confirmation.update([chair()], start), isEmpty);
    expect(
      confirmation.update([
        chair(box: [.42, .41, .62, .91]),
      ], start.add(const Duration(milliseconds: 800))),
      hasLength(1),
    );
  });
  test(
    'class, position, size change, missing frame and long gap reset tracks',
    () {
      for (final changed in [
        [chair(label: '箱子', id: 41)],
        [
          chair(box: [.7, .4, .9, .9]),
        ],
        [chair(occupancy: .02)],
        <ObstacleDetection>[],
      ]) {
        final confirmation = ObstacleConfirmation();
        confirmation.update([chair()], start);
        expect(
          confirmation.update(
            changed,
            start.add(const Duration(milliseconds: 800)),
          ),
          isEmpty,
        );
        expect(
          confirmation.update([
            chair(),
          ], start.add(const Duration(milliseconds: 1600))),
          isEmpty,
        );
      }
      final confirmation = ObstacleConfirmation();
      confirmation.update([chair()], start);
      expect(
        confirmation.update([chair()], start.add(const Duration(seconds: 3))),
        isEmpty,
      );
      expect(confirmation.update([chair()], start), isEmpty);
    },
  );
  test('large central confident person can warn on the first frame', () {
    const person = ObstacleDetection(
      classId: 12,
      label: '行人',
      position: '前方',
      level: 'caution',
      confidence: .95,
      occupancy: .12,
      box: [.3, .4, .7, .9],
    );
    expect(ObstacleConfirmation().update([person], start), [person]);
    expect(chair(label: '行人', id: 12, occupancy: .02).immediate, isFalse);
    expect(chair().immediate, isFalse);
  });
  test(
    'door and stairs ask for observation without inventing closed state',
    () {
      expect(chair(label: '门').message, contains('观察开闭状态'));
      expect(chair(label: '楼梯').message, contains('停下确认'));
      expect(chair(label: '台阶').message, contains('停下确认'));
    },
  );
  test(
    'indoor protocol keeps raw predictions and rejects old or unsafe geometry',
    () {
      final result = ObstacleObservation.fromJson(wire());
      expect(result.detections.single.label, '椅子');
      expect(result.rawPredictions.single['model_label'], 'streetlight');
      for (final mutation in [
        {'profile': 'outdoor'},
        {'policy_version': 1},
        {'distance_available': true},
        {'automatic_detour': true},
        {'detections': <dynamic>[]},
      ]) {
        expect(
          () => ObstacleObservation.fromJson({...wire(), ...mutation}),
          throwsFormatException,
        );
      }
      final bad = wire();
      (bad['detections'] as List).single['box'] = [.6, .4, .4, .9];
      expect(() => ObstacleObservation.fromJson(bad), throwsFormatException);
    },
  );
  test(
    'confirmation, diagnostic retention, pause and speech cooldown work together',
    () async {
      var now = start;
      final warnings = <String>[];
      final monitor = ObstacleMonitor(
        analyze:
            (_, id) async =>
                ObstacleObservation.fromJson({...wire(), 'frame_id': id}),
        clock: () => now,
        onWarning: warnings.add,
        onClear: () {},
        onDiagnostic: (_) => throw StateError('storage failed'),
        automaticExpiry: false,
      );
      monitor.setEnabled(true);
      monitor.setActive(true);
      Future<void> frame() async {
        await monitor.submit(Uint8List(1), now);
        now = now.add(const Duration(milliseconds: 800));
      }

      await frame();
      expect(monitor.warning, isFalse);
      expect(monitor.confirmationStatus, contains('1/2'));
      await frame();
      expect(monitor.warning, isTrue);
      expect(warnings, hasLength(1));
      await frame();
      expect(warnings, hasLength(1));
      monitor.setActive(false);
      monitor.setActive(true);
      await frame();
      expect(monitor.warning, isFalse);
      for (var i = 0; i < 32; i++) {
        await frame();
      }
      final frames = jsonDecode(monitor.exportDiagnostics())['frames'] as List;
      expect(frames, hasLength(30));
      expect(
        frames.last['raw_predictions'][0]['disposition'],
        'filtered_scene',
      );
      monitor.dispose();
    },
  );
  testWidgets('diagnostic dialog exposes filtered model labels', (
    tester,
  ) async {
    final monitor = ObstacleMonitor(
      analyze:
          (_, id) async =>
              ObstacleObservation.fromJson({...wire(), 'frame_id': id}),
      clock: () => start,
      onWarning: (_) {},
      onClear: () {},
      automaticExpiry: false,
    );
    monitor.setEnabled(true);
    monitor.setActive(true);
    await monitor.submit(Uint8List(1), start);
    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: ObstacleAssistPanel(monitor: monitor))),
    );
    await tester.tap(find.byTooltip('识别诊断'));
    await tester.pumpAndSettle();
    expect(find.text('室内避障诊断'), findsOneWidget);
    expect(find.textContaining('streetlight'), findsOneWidget);
    expect(find.textContaining('室内场景已过滤'), findsOneWidget);
    expect(find.text('复制诊断记录'), findsOneWidget);
    await tester.tap(find.text('关闭'));
    await tester.pumpAndSettle();
    monitor.dispose();
  });
}
