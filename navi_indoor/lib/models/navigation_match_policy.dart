import 'path_models.dart';

const double navigationWaypointThreshold = 0.7;
const double navigationCriticalWaypointThreshold = 0.8;

double routeWaypointThreshold({
  required TurnType currentTurn,
  TurnType? nextTurn,
}) {
  final critical =
      currentTurn != TurnType.straight ||
      nextTurn == null ||
      nextTurn != TurnType.straight;
  return critical
      ? navigationCriticalWaypointThreshold
      : navigationWaypointThreshold;
}

bool routeWaypointMatches(double similarity, double threshold) =>
    similarity.isFinite && similarity >= threshold;

const int navigationConfirmationFrames = 2;

// Anchor and critical views need a second observation. Ordinary straight views
// can share recent movement evidence instead of requiring a step per reference.
int navigationRequiredConfirmations(int sequence, double threshold) =>
    sequence == 0 || threshold >= navigationCriticalWaypointThreshold
        ? navigationConfirmationFrames
        : 1;

enum NavigationMatchReason {
  belowThreshold,
  waitingMovement,
  confirming,
  accepted,
  invalid,
  repeatedFrame,
}

class NavigationMatchDecision {
  const NavigationMatchDecision({
    required this.reason,
    required this.confirmations,
    this.requiredConfirmations = navigationConfirmationFrames,
    this.candidateSequence,
  });
  final NavigationMatchReason reason;
  final int confirmations;
  final int requiredConfirmations;
  final int? candidateSequence;
  bool get accepted => reason == NavigationMatchReason.accepted;
  bool get eligible => accepted || reason == NavigationMatchReason.confirming;
  String get message {
    switch (reason) {
      case NavigationMatchReason.belowThreshold:
        return '当前直行窗口内没有达标画面，请核对位置与拍摄方向';
      case NavigationMatchReason.waitingMovement:
        return '移动证据已用完或过期；若已到达此处，请点击下方确认，无需再迈一步';
      case NavigationMatchReason.confirming:
        return '关键画面连续确认 $confirmations / $requiredConfirmations';
      case NavigationMatchReason.accepted:
        return '${requiredConfirmations == 1 ? '画面与移动确认' : '连续画面确认'}通过，跟踪到第 ${candidateSequence! + 1} 个参考点';
      case NavigationMatchReason.invalid:
        return '比对数据无效，本次不推进';
      case NavigationMatchReason.repeatedFrame:
        return '等待新的摄像头画面，本次不推进';
    }
  }
}

/// Straight references are tracked within a bounded window, not by distance.
/// Motion is independent evidence; similar pictures alone cannot consume a route.
/// The caller must end the window BEFORE any turn, pre-turn or endpoint.
class NavigationWaypointTracker {
  int? _target;
  DateTime? _lastObservation;
  DateTime? _lastEligible;
  int _confirmations = 0;
  int? _candidate;
  int _lastSeenMovement = 0;
  DateTime? _movementAt;
  int _movementThrough = -1;
  int _acceptedThrough = -1;
  // At most four nearby samples share a movement event. Neither the time lease
  // nor the sequence bound is refreshed merely by accepting a similar image.
  static const movementLifetime = Duration(seconds: 20);
  static const movementReferenceSpan = 4;
  void reset({int movementCount = 0}) {
    _target = null;
    _lastObservation = null;
    _lastSeenMovement = movementCount;
    _movementAt = null;
    _movementThrough = -1;
    _acceptedThrough = -1;
    interrupt();
  }

  void interrupt() {
    _confirmations = 0;
    _candidate = null;
    _lastEligible = null;
  }

  NavigationMatchDecision observe({
    required int targetSequence,
    required Map<int, double> scores,
    required double threshold,
    required DateTime capturedAt,
    int? forwardLimit,
    int movementCount = 0,
    bool turnInPlace = false,
  }) {
    final requiredFrames = navigationRequiredConfirmations(
      targetSequence,
      threshold,
    );
    if (_target != targetSequence) {
      _target = targetSequence;
      interrupt();
    }
    if (_lastObservation != null &&
        capturedAt.difference(_lastObservation!) <
            const Duration(milliseconds: 600)) {
      return NavigationMatchDecision(
        reason: NavigationMatchReason.repeatedFrame,
        confirmations: _confirmations,
        requiredConfirmations: requiredFrames,
      );
    }
    _lastObservation = capturedAt;
    if (!threshold.isFinite ||
        !scores.containsKey(targetSequence) ||
        scores.values.any((s) => !s.isFinite)) {
      interrupt();
      return NavigationMatchDecision(
        reason: NavigationMatchReason.invalid,
        confirmations: 0,
        requiredConfirmations: requiredFrames,
      );
    }
    if (targetSequence <= _acceptedThrough) {
      interrupt();
      return NavigationMatchDecision(
        reason: NavigationMatchReason.repeatedFrame,
        confirmations: 0,
        requiredConfirmations: requiredFrames,
      );
    }
    if (movementCount > _lastSeenMovement) {
      _lastSeenMovement = movementCount;
      _movementAt = capturedAt;
      _movementThrough = targetSequence + movementReferenceSpan - 1;
    }
    final recentMovement =
        _movementAt != null &&
        capturedAt.difference(_movementAt!) <= movementLifetime &&
        targetSequence <= _movementThrough;
    if (targetSequence > 0 && !recentMovement && !turnInPlace) {
      interrupt();
      return NavigationMatchDecision(
        reason: NavigationMatchReason.waitingMovement,
        confirmations: 0,
        requiredConfirmations: requiredFrames,
      );
    }
    var limit =
        targetSequence == 0 || threshold >= navigationCriticalWaypointThreshold
            ? targetSequence
            : (forwardLimit ?? targetSequence).clamp(
              targetSequence,
              targetSequence + 2,
            );
    if (targetSequence > 0 && !turnInPlace && limit > _movementThrough) {
      limit = _movementThrough;
    }
    int? winner;
    double best = double.negativeInfinity;
    for (var seq = targetSequence; seq <= limit; seq++) {
      final score = scores[seq];
      if (score != null &&
          routeWaypointMatches(score, threshold) &&
          score > best) {
        winner = seq;
        best = score;
      }
    }
    if (winner == null) {
      interrupt();
      return NavigationMatchDecision(
        reason: NavigationMatchReason.belowThreshold,
        confirmations: 0,
        requiredConfirmations: requiredFrames,
      );
    }
    if (_lastEligible == null ||
        capturedAt.difference(_lastEligible!) > const Duration(seconds: 20))
      interrupt();
    _lastEligible = capturedAt;
    // Adjacent ordinary references may exchange rank. Confirm the common lower
    // bound, without requiring indistinguishable hallway images to win a margin.
    _candidate =
        _candidate == null || winner < _candidate! ? winner : _candidate;
    _confirmations++;
    final accepted = _confirmations >= requiredFrames;
    if (accepted) {
      _acceptedThrough = _candidate!;
      if (turnInPlace) {
        // A confirmed physical rotation is new movement evidence for the first
        // nearby outgoing views, even when the step counter did not increment.
        _movementAt = capturedAt;
        _movementThrough = _candidate! + movementReferenceSpan - 1;
      }
    }
    return NavigationMatchDecision(
      reason:
          accepted
              ? NavigationMatchReason.accepted
              : NavigationMatchReason.confirming,
      confirmations: _confirmations,
      requiredConfirmations: requiredFrames,
      candidateSequence: _candidate,
    );
  }
}

/// Critical references must not be absorbed into a straight tracking window.
int navigationForwardLimit({
  required int targetSequence,
  required Set<int> criticalSequences,
  required int lastSequence,
}) {
  var limit = targetSequence;
  if (targetSequence == 0 || criticalSequences.contains(targetSequence))
    return limit;
  for (
    var seq = targetSequence + 1;
    seq <= targetSequence + 2 && seq <= lastSequence;
    seq++
  ) {
    if (criticalSequences.contains(seq)) break;
    limit = seq;
  }
  return limit;
}
