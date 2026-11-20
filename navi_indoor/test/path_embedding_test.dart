import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:indoor_navigation_app/models/embedding_values.dart';
import 'package:indoor_navigation_app/models/path_models.dart';
import 'package:indoor_navigation_app/services/dinov2_service.dart';

void main() {
  test('PostgreSQL mixed numeric arrays decode without int/double cast errors', () {
    expect(parseNumericValues(jsonDecode('[0,1,0.25,-0.5]')), [0.0,1.0,0.25,-0.5]);
    expect(parseNumericValues('[0,0.5]'), [0.0,0.5]);
    expect(() => parseNumericValues([null]), throwsFormatException);
  });

  test('zero, malformed and non-finite embeddings are excluded', () {
    expect(isUsableEmbedding(List.filled(768, 0.0)), isFalse);
    expect(isUsableEmbedding([1.0]), isFalse);
    expect(isUsableEmbedding(List.filled(768, double.nan)), isFalse);
    expect(isUsableEmbedding([1.0, ...List.filled(767, 0.0)]), isTrue);
  });

  test('saved waypoint accepts integer zero in embedding and confidence', () {
    final point = PathWaypoint.fromJson({
      'id':'point', 'embedding':[1,...List.filled(767,0)], 'heading':0,
      'heading_change':0, 'turn_type':'straight', 'is_decision_point':false,
      'sequence_number':0, 'timestamp':'2026-10-29T17:00:00Z',
      'people_confidence_scores':[0,0.5],
    });
    expect(point.embedding.first, 1.0);
    expect(point.peopleConfidenceScores, [0.0,0.5]);
  });

  late Directory temp;
  late File frame;
  setUp(() async {
    temp = await Directory.systemTemp.createTemp('navi-embedding-test-');
    frame = await File('${temp.path}/frame.jpg').writeAsBytes([1,2,3]);
  });
  tearDown(() async { await temp.delete(recursive:true); });

  test('network errors and zero server response throw instead of saving fake features', () async {
    for (final response in [http.Response('unavailable',503),
      http.Response(jsonEncode({'embedding':List.filled(768,0)}),200),
      http.Response('{}',200)]) {
      final service = ClipService(serverUrl:'http://example.test',
        embeddingClient:MockClient((_) async => response));
      await expectLater(service.generatePreprocessedEmbedding(frame), throwsException);
    }
    final service = ClipService(serverUrl:'http://example.test',
      embeddingClient:MockClient((_) async => throw const SocketException('offline')));
    await expectLater(service.generatePreprocessedEmbedding(frame), throwsException);
  });

  test('successful multipart feature request returns real numeric values', () async {
    final service = ClipService(serverUrl:'http://example.test',
      embeddingClient:MockClient((request) async {
        expect(request.url.path, '/encode/preprocessed');
        expect(request.headers['content-type'], contains('multipart/form-data'));
        return http.Response(jsonEncode({'embedding':[1,...List.filled(767,0)]}),200);
      }));
    final result = await service.generatePreprocessedEmbedding(frame);
    expect(result.length,768);
    expect(result.first,1.0);
  });
}
