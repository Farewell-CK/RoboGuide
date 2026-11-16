import 'dart:math' as math;

class ObstacleDetection {
  const ObstacleDetection({
    required this.classId,
    required this.label,
    required this.position,
    required this.level,
    required this.confidence,
    required this.occupancy,
    required this.box,
  });
  final int classId;
  final String label, position, level;
  final double confidence, occupancy;
  final List<double> box;

  factory ObstacleDetection.fromJson(Map<String, dynamic> json) {
    final box =
        (json['box'] as List).map((v) => (v as num).toDouble()).toList();
    final score = (json['confidence'] as num).toDouble();
    final area = (json['occupancy'] as num).toDouble();
    final label = json['label'];
    if (box.length != 4 ||
        box.any((v) => !v.isFinite || v < 0 || v > 1) ||
        box[0] >= box[2] ||
        box[1] >= box[3] ||
        !score.isFinite ||
        score < 0 ||
        score > 1 ||
        !area.isFinite ||
        area <= 0 ||
        area > 1 ||
        label is! String ||
        label.length > 24 ||
        !['notice', 'caution'].contains(json['level']) ||
        !['左前方', '前方', '右前方'].contains(json['position']) ||
        json['distance_m'] != null) {
      throw const FormatException('Invalid obstacle detection');
    }
    return ObstacleDetection(
      classId: (json['class_id'] as num).toInt(),
      label: label,
      position: json['position'],
      level: json['level'],
      confidence: score,
      occupancy: area,
      box: List.unmodifiable(box),
    );
  }

  // Only a large, confident central person bypasses temporal confirmation.
  bool get immediate =>
      label == '行人' &&
      level == 'caution' &&
      confidence >= .85 &&
      occupancy >= .06 &&
      box[0] < .65 &&
      box[2] > .35;

  bool matches(ObstacleDetection other) {
    if (label != other.label || classId != other.classId) return false;
    final areaRatio =
        math.min(occupancy, other.occupancy) /
        math.max(occupancy, other.occupancy);
    final centerShift =
        ((box[0] + box[2] - other.box[0] - other.box[2]) / 2).abs();
    if (areaRatio < .5 || centerShift > .15) return false;
    final intersection =
        math.max(
          0.0,
          math.min(box[2], other.box[2]) - math.max(box[0], other.box[0]),
        ) *
        math.max(
          0.0,
          math.min(box[3], other.box[3]) - math.max(box[1], other.box[1]),
        );
    final union =
        (box[2] - box[0]) * (box[3] - box[1]) +
        (other.box[2] - other.box[0]) * (other.box[3] - other.box[1]) -
        intersection;
    return union > 0 && intersection / union >= .25;
  }

  String get message {
    final prefix = '${position}识别到$label';
    if (label == '门') return '$prefix，请观察开闭状态。';
    if (label == '楼梯' || label == '台阶') return '$prefix，请停下确认。';
    return '$prefix，${level == 'caution' ? '请停下观察。' : '请留意周围。'}';
  }
}

class ObstacleConfirmation {
  final List<(ObstacleDetection, int)> _tracks = [];
  DateTime? _lastCapture;
  void clear() {
    _tracks.clear();
    _lastCapture = null;
  }

  List<ObstacleDetection> update(
    List<ObstacleDetection> current,
    DateTime capture,
  ) {
    if (_lastCapture != null &&
        (capture.difference(_lastCapture!) > const Duration(seconds: 2) ||
            !capture.isAfter(_lastCapture!)))
      clear();
    _lastCapture = capture;
    final previous = List<(ObstacleDetection, int)>.from(_tracks);
    _tracks.clear();
    final confirmed = <ObstacleDetection>[];
    for (final detection in current) {
      final index = previous.indexWhere((track) => detection.matches(track.$1));
      final count =
          index < 0 ? 1 : math.min(2, previous.removeAt(index).$2 + 1);
      _tracks.add((detection, count));
      if (count >= 2 || detection.immediate) confirmed.add(detection);
    }
    return confirmed;
  }
}
