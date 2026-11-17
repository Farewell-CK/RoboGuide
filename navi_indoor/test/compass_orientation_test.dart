import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/models/orientation_math.dart';
import 'package:indoor_navigation_app/widgets/compass_painter.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Aligned headings allow up to five degrees, including across north', () {
    expect(isDepartureHeadingAligned(90, 93), isTrue);
    expect(isDepartureHeadingAligned(90, 95), isTrue);
    expect(isDepartureHeadingAligned(90, 95.1), isFalse);
    expect(isDepartureHeadingAligned(359, 1), isTrue);
    expect(isDepartureHeadingAligned(double.nan, 90), isFalse);
  });

  test(
    'A left turn remains left across north and with signed compass values',
    () {
      expect(relativeTargetHeading(90, 0), -90);
      expect(relativeTargetHeading(10, 350), -20);
      expect(relativeTargetHeading(-10, 10), 20);
    },
  );

  Future<void> expectTarget({
    required double current,
    required double target,
    required int x,
    required int y,
  }) async {
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder);
    CompassPainter(
      currentHeading: current,
      targetHeading: target,
    ).paint(canvas, const Size(220, 220));
    final picture = recorder.endRecording();
    final image = await picture.toImage(220, 220);
    final bytes = (await image.toByteData(format: ui.ImageByteFormat.rawRgba))!;
    List<int> pixel(int px, int py) {
      final index = (py * 220 + px) * 4;
      return [
        bytes.getUint8(index),
        bytes.getUint8(index + 1),
        bytes.getUint8(index + 2),
      ];
    }

    // Check actual rendered pixels, not just the angle helper.
    expect(pixel(x, y), [
      33,
      150,
      243,
    ], reason: 'Blue target must be phone-relative');
    expect(pixel(110, 80), [
      255,
      255,
      255,
    ], reason: 'Phone-forward arrow must stay up');
    image.dispose();
    picture.dispose();
  }

  test('Facing east with an east target draws target straight ahead', () async {
    await expectTarget(current: 90, target: 90, x: 110, y: 45);
  });
  test('Facing east with a north target draws target to the left', () async {
    await expectTarget(current: 90, target: 0, x: 45, y: 110);
  });
  test('Facing east with a south target draws target to the right', () async {
    await expectTarget(current: 90, target: 180, x: 175, y: 110);
  });
}
