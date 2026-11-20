import 'orientation_math.dart';
import 'path_models.dart';

/// Fill a missing turn only when a substantial heading change persists in the
/// next sample. A one-frame compass spike is insufficient. Stored labels win.
/// The returned copy is navigation-only and never writes back to the database.
List<PathWaypoint> navigationWaypointsWithTurns(List<PathWaypoint> source) {
  final sorted = [...source]
    ..sort((a, b) => a.sequenceNumber.compareTo(b.sequenceNumber));
  final inferred = <int, TurnType>{};
  for (var i = 1; i < sorted.length - 1; i++) {
    final previous = sorted[i - 1];
    final point = sorted[i];
    final next = sorted[i + 1];
    if (point.turnType != TurnType.straight ||
        previous.sequenceNumber + 1 != point.sequenceNumber ||
        point.sequenceNumber + 1 != next.sequenceNumber ||
        !previous.heading.isFinite ||
        !point.heading.isFinite ||
        !next.heading.isFinite)
      continue;
    final change = relativeTargetHeading(previous.heading, point.heading);
    final continuation = relativeTargetHeading(point.heading, next.heading);
    final sustained = relativeTargetHeading(previous.heading, next.heading);
    if (change.abs() < 45 ||
        change.abs() > 135 ||
        continuation.abs() > 25 ||
        sustained.abs() < 55 ||
        change.sign != sustained.sign)
      continue;
    if (i >= 2 &&
        (!sorted[i - 2].heading.isFinite ||
            sorted[i - 2].sequenceNumber + 1 != previous.sequenceNumber ||
            relativeTargetHeading(
                  sorted[i - 2].heading,
                  previous.heading,
                ).abs() >
                25))
      continue;
    inferred[point.sequenceNumber] =
        change < 0 ? TurnType.left : TurnType.right;
  }
  return [
    for (final point in sorted)
      if (!inferred.containsKey(point.sequenceNumber))
        point
      else
        PathWaypoint(
          id: point.id,
          embedding: point.embedding,
          heading: point.heading,
          headingChange: relativeTargetHeading(
            sorted
                .firstWhere((w) => w.sequenceNumber == point.sequenceNumber - 1)
                .heading,
            point.heading,
          ),
          turnType: inferred[point.sequenceNumber]!,
          isDecisionPoint: true,
          landmarkDescription: point.landmarkDescription,
          distanceFromPrevious: point.distanceFromPrevious,
          sequenceNumber: point.sequenceNumber,
          timestamp: point.timestamp,
          peopleDetected: point.peopleDetected,
          peopleCount: point.peopleCount,
          peopleConfidenceScores: point.peopleConfidenceScores,
        ),
  ];
}

/// Verify the direction and end heading of a turn, not just any compass change.
bool navigationTurnConfirmedInPlace({
  required TurnType turn,
  required double incomingHeading,
  required double targetHeading,
  required double? currentHeading,
}) {
  if (turn == TurnType.straight ||
      currentHeading == null ||
      !incomingHeading.isFinite ||
      !targetHeading.isFinite ||
      !currentHeading.isFinite)
    return false;
  final rotation = relativeTargetHeading(incomingHeading, currentHeading);
  final directionMatches =
      turn == TurnType.left ? rotation <= -25 : rotation >= 25;
  return directionMatches &&
      relativeTargetHeading(currentHeading, targetHeading).abs() <= 35;
}
