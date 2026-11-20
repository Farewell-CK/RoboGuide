import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'navigation_upload.dart';
import 'obstacle_confirmation.dart';
export 'obstacle_confirmation.dart';

class ObstacleObservation {
  const ObstacleObservation(
    this.frameId,
    this.message,
    this.level, {
    this.detections = const [],
    this.rawPredictions = const [],
    this.inferenceMs = 0,
  });
  final String frameId;
  final String message;
  final String level;
  final List<ObstacleDetection> detections;
  final List<Map<String, dynamic>> rawPredictions;
  final int inferenceMs;
  bool get hasObstacle => level == 'notice' || level == 'caution';
  factory ObstacleObservation.fromJson(Map<String, dynamic> json) {
    final level = json['level'];
    if (json['policy_version'] != 2 ||
        json['profile'] != 'indoor' ||
        !['unknown', 'notice', 'caution'].contains(level) ||
        json['distance_available'] != false ||
        json['automatic_detour'] != false ||
        json['frame_id'] is! String ||
        json['message'] is! String ||
        (json['message'] as String).length > 160) {
      throw const FormatException('Invalid obstacle observation');
    }
    final detections =
        (json['detections'] as List)
            .map(
              (d) => ObstacleDetection.fromJson(Map<String, dynamic>.from(d)),
            )
            .toList();
    final raw =
        (json['raw_predictions'] as List).map((d) {
          final p = Map<String, dynamic>.from(d);
          if (p['model_label'] is! String ||
              (p['model_label'] as String).length > 80 ||
              p['confidence'] is! num ||
              !(p['confidence'] as num).isFinite ||
              p['occupancy'] is! num ||
              !(p['occupancy'] as num).isFinite ||
              ![
                'candidate',
                'filtered_scene',
                'below_confidence_or_area',
              ].contains(p['disposition'])) {
            throw const FormatException('Invalid raw prediction');
          }
          return <String, dynamic>{
            'class_id': p['class_id'],
            'model_label': p['model_label'],
            'confidence': p['confidence'],
            'occupancy': p['occupancy'],
            'disposition': p['disposition'],
          };
        }).toList();
    if (detections.length > 4 ||
        raw.length > 12 ||
        (level != 'unknown' && detections.isEmpty) ||
        (level == 'unknown' && detections.isNotEmpty)) {
      throw const FormatException('Inconsistent obstacle observation');
    }
    return ObstacleObservation(
      json['frame_id'],
      json['message'],
      level,
      detections: detections,
      rawPredictions: raw,
      inferenceMs: (json['inference_ms'] as num?)?.toInt() ?? 0,
    );
  }
}

class ObstacleApi {
  ObstacleApi(String base)
    : _endpoint = Uri.parse(
        '${base.replaceAll(RegExp(r'/+$'), '')}/obstacles/analyze',
      );
  final Uri _endpoint;
  http.Client _client = http.Client();
  bool _closed = false;
  Future<ObstacleObservation> analyze(Uint8List bytes, String frameId) async {
    final client = _client;
    try {
      return await (() async {
        final packed = await prepareNavigationUpload(bytes);
        if (_closed) throw StateError('Closed');
        final request =
            http.MultipartRequest('POST', _endpoint)
              ..fields['frame_id'] = frameId
              ..files.add(
                http.MultipartFile.fromBytes(
                  'file',
                  packed,
                  filename: 'obstacle.jpg',
                ),
              );
        final response = await client.send(request);
        final body = await response.stream.bytesToString();
        if (body.length > 65536)
          throw const FormatException('Oversized observation');
        if (response.statusCode != 200)
          throw StateError('Observation unavailable');
        return ObstacleObservation.fromJson(
          jsonDecode(body) as Map<String, dynamic>,
        );
      })().timeout(const Duration(milliseconds: 2800));
    } catch (_) {
      client.close();
      if (!_closed && identical(_client, client)) _client = http.Client();
      rethrow;
    }
  }

  void dispose() {
    _closed = true;
    _client.close();
  }
}

typedef ObstacleAnalyzer =
    Future<ObstacleObservation> Function(Uint8List bytes, String id);

/// One request at a time, capture-time expiry and generation isolation. No frame
/// queue: an old obstacle must not be presented as the current scene.
class ObstacleMonitor extends ChangeNotifier {
  ObstacleMonitor({
    required this.analyze,
    required this.onWarning,
    required this.onClear,
    this.onDiagnostic,
    DateTime Function()? clock,
    this.maxAge = const Duration(seconds: 3),
    bool automaticExpiry = true,
  }) : _clock = clock ?? DateTime.now {
    if (automaticExpiry)
      _expiry = Timer.periodic(
        const Duration(milliseconds: 250),
        (_) => expire(),
      );
  }
  final ObstacleAnalyzer analyze;
  final void Function(String message) onWarning;
  final VoidCallback onClear;
  final void Function(Map<String, dynamic>)? onDiagnostic;
  final ObstacleConfirmation _confirmation = ObstacleConfirmation();
  final List<Map<String, dynamic>> _history = [];
  List<Map<String, dynamic>> rawPredictions = [];
  int inferenceMs = 0;
  String confirmationStatus = '尚未识别';
  String exportDiagnostics() => const JsonEncoder.withIndent(
    '  ',
  ).convert({'profile': 'indoor', 'policy_version': 2, 'frames': _history});
  final DateTime Function() _clock;
  final Duration maxAge;
  bool enabled = false;
  bool active = false;
  bool _closed = false, _busy = false;
  int _generation = 0, _serial = 0;
  DateTime? _lastCapture, _observedCapture, _announcedAt;
  String? _announced;
  Timer? _expiry;
  String status = '仅画面识别，不测距、不自动绕行。';
  bool warning = false;

  void setEnabled(bool value) {
    if (_closed || enabled == value) return;
    enabled = value;
    _invalidate(value ? '已开启，开始导航后识别；不测距、不自动绕行。' : '视觉避障已关闭。');
  }

  void setActive(bool value) {
    if (_closed || active == value) return;
    active = value;
    _invalidate(
      !enabled
          ? '视觉避障已关闭。'
          : value
          ? '等待新画面；不测距、不自动绕行。'
          : '导航未运行，障碍识别暂停。',
    );
  }

  void _invalidate(String text) {
    _generation++;
    _observedCapture = null;
    _lastCapture = null;
    _announced = null;
    _announcedAt = null;
    _confirmation.clear();
    rawPredictions = [];
    confirmationStatus = '等待新画面';
    warning = false;
    status = text;
    onClear();
    notifyListeners();
  }

  Future<void> submit(Uint8List bytes, DateTime capturedAt) async {
    if (_closed ||
        !enabled ||
        !active ||
        _busy ||
        _clock().difference(capturedAt) > maxAge ||
        (_lastCapture != null &&
            capturedAt.difference(_lastCapture!) <
                const Duration(milliseconds: 700)))
      return;
    _busy = true;
    _lastCapture = capturedAt;
    final generation = _generation;
    final id = '$generation-${++_serial}';
    try {
      final result = await analyze(bytes, id).timeout(maxAge);
      if (_closed || generation != _generation || !active || !enabled) return;
      if (result.frameId != id || _clock().difference(capturedAt) > maxAge) {
        _unavailable('画面结果已过期，等待新画面。');
        return;
      }
      _observedCapture = capturedAt;
      final confirmed = _confirmation.update(result.detections, capturedAt);
      rawPredictions = result.rawPredictions;
      inferenceMs = result.inferenceMs;
      warning = confirmed.isNotEmpty;
      confirmationStatus =
          warning
              ? (confirmed.first.immediate ? '近前方行人即时提醒' : '连续2帧确认')
              : result.detections.isNotEmpty
              ? '候选1/2，等待下一帧；请留意实际环境'
              : '本帧无可提示障碍';
      status =
          warning
              ? confirmed.first.message
              : result.detections.isNotEmpty
              ? '发现疑似物体，正在短时确认；请留意实际环境。'
              : result.message;
      final diagnostic = <String, dynamic>{
        'frame_id': id,
        'captured_at': capturedAt.toIso8601String(),
        'age_ms': _clock().difference(capturedAt).inMilliseconds,
        'inference_ms': inferenceMs,
        'decision': confirmationStatus,
        'message': status,
        'raw_predictions': rawPredictions,
        'candidates':
            result.detections
                .map(
                  (d) => {
                    'label': d.label,
                    'class_id': d.classId,
                    'box': d.box,
                    'confidence': d.confidence,
                    'occupancy': d.occupancy,
                  },
                )
                .toList(),
      };
      _history.add(diagnostic);
      if (_history.length > 30) _history.removeAt(0);
      try {
        onDiagnostic?.call(diagnostic);
      } catch (_) {
        // Diagnostics must never interrupt obstacle or route guidance.
      }
      notifyListeners();
      if (warning) {
        final now = _clock();
        if (_announced != status ||
            _announcedAt == null ||
            now.difference(_announcedAt!) >= const Duration(seconds: 6)) {
          _announced = status;
          _announcedAt = now;
          onWarning(status);
        }
      } else {
        _announced = null;
        _announcedAt = null;
        onClear();
      }
    } catch (_) {
      if (!_closed && generation == _generation)
        _unavailable('障碍识别暂不可用，请留意实际环境。');
    } finally {
      _busy = false;
    }
  }

  void _unavailable(String message) {
    _confirmation.clear();
    rawPredictions = [];
    confirmationStatus = '结果不可用，重新确认';
    _observedCapture = null;
    warning = false;
    status = message;
    onClear();
    notifyListeners();
  }

  void expire() {
    if (!_closed &&
        _observedCapture != null &&
        _clock().difference(_observedCapture!) > maxAge) {
      _unavailable('画面已过期，等待更新；请留意实际环境。');
    }
  }

  @override
  void dispose() {
    _closed = true;
    _generation++;
    _expiry?.cancel();
    onClear();
    super.dispose();
  }
}
