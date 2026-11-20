import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/models/navigation_match_policy.dart';
import 'package:indoor_navigation_app/models/navigation_turns.dart';
import 'package:indoor_navigation_app/models/path_models.dart';

List<PathWaypoint> points(
  List<double> headings, {
  Map<int, TurnType> turns = const {},
}) => [
  for (var i = 0; i < headings.length; i++)
    PathWaypoint(
      id: '$i',
      embedding: [1, ...List.filled(767, 0.0)],
      heading: headings[i],
      headingChange: 0,
      turnType: turns[i] ?? TurnType.straight,
      isDecisionPoint: false,
      sequenceNumber: i,
      timestamp: DateTime(2026, 11, 5),
    ),
];

void main() {
  test(
    'recorded persistent 60-degree turn becomes a protected left turn without changing stored points',
    () {
      // Headings from the user's 27-reference route, displayed points 14 to 19.
      final source = points([
        122.1885,
        121.8999,
        115.0495,
        55.0195,
        35.4857,
        32.8421,
      ]);
      final tracked = navigationWaypointsWithTurns(source);
      expect(tracked[3].turnType, TurnType.left);
      expect(tracked[3].headingChange, closeTo(-60.03, .01));
      expect(tracked[3].isDecisionPoint, isTrue);
      expect(tracked[3].embedding, same(source[3].embedding));
      expect(source[3].turnType, TurnType.straight);
      expect(source[3].isDecisionPoint, isFalse);
      expect(
        routeWaypointThreshold(
          currentTurn: tracked[2].turnType,
          nextTurn: tracked[3].turnType,
        ),
        .8,
      );
      expect(
        navigationForwardLimit(
          targetSequence: 1,
          criticalSequences: {2, 3, 5},
          lastSequence: 5,
        ),
        1,
      );
    },
  );

  test(
    'one-frame compass spike and gradual small changes do not create a turn',
    () {
      for (final headings in [
        [115.0, 115.0, 55.0, 115.0],
        [115.0, 100.0, 85.0, 70.0],
      ]) {
        expect(
          navigationWaypointsWithTurns(
            points(headings),
          ).every((p) => p.turnType == TurnType.straight),
          isTrue,
        );
      }
    },
  );

  test('inference handles north crossing and preserves explicit labels', () {
    final tracked = navigationWaypointsWithTurns(points([350, 352, 60, 65]));
    expect(tracked[2].turnType, TurnType.right);
    final labelled = navigationWaypointsWithTurns(
      points([115, 115, 55, 35], turns: {2: TurnType.right}),
    );
    expect(labelled[2].turnType, TurnType.right);
  });

  test('a gap or nonfinite heading cannot supply persistent turn evidence', () {
    final gap = points([115, 115, 55, 35])..removeAt(1);
    expect(
      navigationWaypointsWithTurns(
        gap,
      ).every((p) => p.turnType == TurnType.straight),
      isTrue,
    );
    expect(
      navigationWaypointsWithTurns(
        points([115, double.nan, 55, 35]),
      ).every((p) => p.turnType == TurnType.straight),
      isTrue,
    );
  });

  test(
    'in-place turn requires the recorded direction and outgoing heading',
    () {
      bool left(double? heading) => navigationTurnConfirmedInPlace(
        turn: TurnType.left,
        incomingHeading: 115.0495,
        targetHeading: 55.0195,
        currentHeading: heading,
      );
      expect(left(55), isTrue);
      expect(left(35.7), isTrue);
      expect(left(115), isFalse);
      expect(left(160), isFalse);
      expect(left(0), isFalse);
      expect(left(null), isFalse);
      expect(left(double.nan), isFalse);
      expect(
        navigationTurnConfirmedInPlace(
          turn: TurnType.right,
          incomingHeading: 350,
          targetHeading: 60,
          currentHeading: 65,
        ),
        isTrue,
      );
      expect(
        navigationTurnConfirmedInPlace(
          turn: TurnType.straight,
          incomingHeading: 115,
          targetHeading: 55,
          currentHeading: 55,
        ),
        isFalse,
      );
    },
  );
}
