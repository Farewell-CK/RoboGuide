import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/models/localization_candidates.dart';

void main() {
  Map<String, dynamic> candidate(String node, double score) => {
    'node_id': node,
    'score': score,
  };

  test('Repeated views cannot hide the second candidate node', () {
    final ranked = rankLocalizationCandidates([
      candidate('office', 0.7),
      candidate('office', 0.9),
      candidate('office', 0.85),
      candidate('lift', 0.8),
      candidate('corner', 0.6),
      candidate('other', 0.5),
    ]);
    expect(ranked.map((c) => c['node_id']), ['office', 'lift', 'corner']);
    expect(ranked.first['score'], 0.9);
  });

  test('Finite negative scores are reported without fabricating zero', () {
    final ranked = rankLocalizationCandidates([
      candidate('invalid', double.nan),
      candidate('a', -0.2),
      candidate('b', -0.1),
    ]);
    expect(ranked.map((c) => c['node_id']), ['b', 'a']);
    expect(rankLocalizationCandidates([]), isEmpty);
  });
}
