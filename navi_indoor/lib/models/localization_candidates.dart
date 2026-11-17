/// Keep the strongest reference per node, so repeated views of the same node
/// do not hide the runner-up location in diagnostic results.
List<Map<String, dynamic>> rankLocalizationCandidates(
  Iterable<Map<String, dynamic>> candidates, {
  int limit = 3,
}) {
  final byNode = <String, Map<String, dynamic>>{};
  for (final item in candidates) {
    final score = item['score'] as double;
    if (!score.isFinite) continue;
    final node = item['node_id'] as String;
    final previous = byNode[node];
    if (previous == null || score > (previous['score'] as double)) {
      byNode[node] = item;
    }
  }
  final ranked =
      byNode.values.toList()..sort(
        (a, b) => (b['score'] as double).compareTo(a['score'] as double),
      );
  return ranked.take(limit).toList();
}
