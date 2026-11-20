import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/models/path_models.dart';
import 'package:indoor_navigation_app/models/navigation_match_policy.dart';

void main() {
  test(
    'Recorded hallway score 0.818 progresses instead of waiting for 0.87',
    () {
      final threshold = routeWaypointThreshold(
        currentTurn: TurnType.straight,
        nextTurn: TurnType.straight,
      );
      expect(threshold, 0.7);
      expect(routeWaypointMatches(0.818, threshold), isTrue);
      expect(routeWaypointMatches(0.7, threshold), isTrue);
      expect(routeWaypointMatches(0.6999, threshold), isFalse);
    },
  );

  test('Hallway-like 0.75 scores cannot advance turns or report arrival', () {
    final thresholds = [
      routeWaypointThreshold(
        currentTurn: TurnType.straight,
        nextTurn: TurnType.left,
      ),
      routeWaypointThreshold(
        currentTurn: TurnType.left,
        nextTurn: TurnType.straight,
      ),
      routeWaypointThreshold(
        currentTurn: TurnType.straight,
        nextTurn: TurnType.right,
      ),
      routeWaypointThreshold(
        currentTurn: TurnType.right,
        nextTurn: TurnType.straight,
      ),
      routeWaypointThreshold(currentTurn: TurnType.straight),
    ];
    for (final threshold in thresholds) {
      expect(threshold, 0.8);
      expect(routeWaypointMatches(0.75, threshold), isFalse);
      expect(routeWaypointMatches(0.8, threshold), isTrue);
      expect(routeWaypointMatches(0.885, threshold), isTrue);
    }
  });

  test('Non-finite matching scores never advance a route', () {
    expect(routeWaypointMatches(double.nan, 0.7), isFalse);
    expect(routeWaypointMatches(double.infinity, 0.7), isFalse);
  });
}
