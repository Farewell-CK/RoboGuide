import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/services/dinov2_service.dart';

void main() {
  final service = ClipService(serverUrl: 'http://test.invalid');
  QualifiedFrame frame(String name, double score) => QualifiedFrame(
    frame: CapturedFrame(
      imageFile: File('unused.jpg'),
      embedding: [1],
      timestamp: DateTime(2026, 11, 4),
      frameIndex: 0,
    ),
    bestMatch: EmbeddingMatch(placeName: name, similarity: score, nodeId: name),
  );

  test('Reported 0.5991 DINOv2 match survives the final confirmation gate', () {
    final result = service.resolveLocalizationMatches([
      frame('办公室门口', 0.5991),
      frame('电梯口', 0.4957),
    ], []);
    expect(result.success, isTrue);
    expect(result.nodeId, '办公室门口');
    expect(result.embeddingSimilarity, 0.5991);
  });

  test('DINOv2 threshold is strict and rejects scores at or below 0.5', () {
    for (final score in [0.5, 0.49, 0.0]) {
      expect(
        service.resolveLocalizationMatches([
          frame('office', score),
        ], []).success,
        isFalse,
      );
    }
    expect(
      service.resolveLocalizationMatches([frame('office', 0.5001)], []).success,
      isTrue,
    );
    expect(service.resolveLocalizationMatches([], []).success, isFalse);
  });
}
