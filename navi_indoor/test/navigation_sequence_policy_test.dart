import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/models/navigation_match_policy.dart';

void main() {
  final start = DateTime(2026, 11, 4);
  test(
    'actual second-point trace passes without winning a competitor margin',
    () {
      final trace =
          jsonDecode(
                File(
                  'test/fixtures/hallway_second_point_stall.json',
                ).readAsStringSync(),
              )
              as List;
      final tracker = NavigationWaypointTracker();
      NavigationMatchDecision? result;
      for (var i = 0; i < 1; i++) {
        final row = trace[i] as Map;
        final scores = <int, double>{
          for (final c in row['candidates'])
            (c['sequence'] as num).toInt(): (c['score'] as num).toDouble(),
          1: (row['target_score'] as num).toDouble(),
        };
        // Old trace has no motion sensor. Supply an explicit new movement event
        // to test the visual rule; this is not a replay of physical ground truth.
        result = tracker.observe(
          targetSequence: 1,
          scores: scores,
          threshold: .7,
          capturedAt: start.add(Duration(seconds: i * 2)),
          forwardLimit: 3,
          movementCount: 1,
        );
      }
      expect(result!.accepted, isTrue);
      expect(result.candidateSequence, inInclusiveRange(1, 3));
      expect(trace.where((r) => r['target_score'] >= .7), hasLength(14));
    },
  );

  test(
    'old stationary hallway trace cannot consume five references without movement',
    () {
      final trace =
          jsonDecode(
                File(
                  'test/fixtures/hallway_false_progress.json',
                ).readAsStringSync(),
              )
              as List;
      final tracker = NavigationWaypointTracker();
      var next = 0;
      // Two fresh observations of the first known view establish the anchor.
      for (var i = 0; i < 2; i++) {
        final row = trace.first as Map;
        final d = tracker.observe(
          targetSequence: next,
          scores: {0: (row['target_score'] as num).toDouble()},
          threshold: .7,
          capturedAt: start.add(Duration(seconds: i * 2)),
        );
        if (d.accepted) next = d.candidateSequence! + 1;
      }
      expect(next, 1);
      for (var i = 1; i < trace.length; i++) {
        final row = trace[i] as Map;
        final scores = <int, double>{
          for (final c in row['candidates'])
            (c['sequence'] as num).toInt(): (c['score'] as num).toDouble(),
          (row['target'] as num).toInt():
              (row['target_score'] as num).toDouble(),
        };
        // Missing scores are not reconstructed. All these rows recorded target 1+.
        if (!scores.containsKey(next)) continue;
        final d = tracker.observe(
          targetSequence: next,
          scores: scores,
          threshold: .7,
          forwardLimit: 3,
          capturedAt: start.add(Duration(seconds: (i + 2) * 2)),
        );
        expect(d.reason, NavigationMatchReason.waitingMovement);
      }
      expect(next, 1);
    },
  );

  test(
    'new movement plus one straight view advances even if a past reference wins',
    () {
      final tracker = NavigationWaypointTracker();
      NavigationMatchDecision observe(int target, int second, int motion) =>
          tracker.observe(
            targetSequence: target,
            scores: {0: .96, 1: .81, 2: .5},
            threshold: .7,
            movementCount: motion,
            capturedAt: start.add(Duration(seconds: second)),
          );
      expect(observe(0, 0, 0).confirmations, 1);
      expect(observe(0, 2, 0).accepted, isTrue);
      expect(observe(1, 4, 0).reason, NavigationMatchReason.waitingMovement);
      expect(observe(1, 6, 1).accepted, isTrue);
      expect(observe(1, 8, 1).reason, NavigationMatchReason.repeatedFrame);
      expect(observe(2, 10, 1).reason, NavigationMatchReason.belowThreshold);
      expect(observe(2, 12, 2).reason, NavigationMatchReason.belowThreshold);
    },
  );

  test(
    'straight view selects within the bounded window without waiting for another request',
    () {
      final tracker = NavigationWaypointTracker();
      final a = tracker.observe(
        targetSequence: 1,
        scores: {0: .95, 1: .6, 2: .83, 3: .8, 4: .99},
        threshold: .7,
        movementCount: 1,
        forwardLimit: 3,
        capturedAt: start,
      );
      expect(a.accepted, isTrue);
      expect(a.requiredConfirmations, 1);
      expect(a.candidateSequence, 2);
      final b = tracker.observe(
        targetSequence: 3,
        scores: {0: .96, 1: .6, 2: .65, 3: .85, 4: .99},
        threshold: .7,
        movementCount: 2,
        forwardLimit: 3,
        capturedAt: start.add(const Duration(seconds: 2)),
      );
      expect(b.accepted, isTrue);
      expect(b.candidateSequence, 3);
    },
  );

  test(
    'turn, pre-turn and endpoint cannot be skipped or absorbed into a straight window',
    () {
      final critical = {4, 5, 6};
      expect(
        navigationForwardLimit(
          targetSequence: 0,
          criticalSequences: critical,
          lastSequence: 6,
        ),
        0,
      );
      expect(
        navigationForwardLimit(
          targetSequence: 1,
          criticalSequences: critical,
          lastSequence: 6,
        ),
        3,
      );
      expect(
        navigationForwardLimit(
          targetSequence: 2,
          criticalSequences: critical,
          lastSequence: 6,
        ),
        3,
      );
      for (final target in critical) {
        expect(
          navigationForwardLimit(
            targetSequence: target,
            criticalSequences: critical,
            lastSequence: 6,
          ),
          target,
        );
        final tracker = NavigationWaypointTracker();
        for (var i = 0; i < 3; i++) {
          final d = tracker.observe(
            targetSequence: target,
            scores: {target: .79, target + 1: .99},
            threshold: .8,
            movementCount: 1,
            forwardLimit: target + 2,
            capturedAt: start.add(Duration(seconds: 2 * i)),
          );
          expect(d.accepted, isFalse);
        }
      }
    },
  );

  test(
    'recorded turn can be confirmed in place, but still needs two above-threshold views',
    () {
      final tracker = NavigationWaypointTracker();
      for (var i = 0; i < 2; i++) {
        final d = tracker.observe(
          targetSequence: 5,
          scores: {5: .85, 6: .99},
          threshold: .8,
          turnInPlace: true,
          capturedAt: start.add(Duration(seconds: 2 * i)),
        );
        expect(d.accepted, i == 1);
        expect(d.candidateSequence, 5);
      }
    },
  );

  test(
    'repeat timestamps, weak frames, long gaps and interruption break stale evidence',
    () {
      final tracker = NavigationWaypointTracker();
      NavigationMatchDecision observe(int ms, {double score = .9}) =>
          tracker.observe(
            targetSequence: 0,
            scores: {0: score},
            threshold: .7,
            capturedAt: start.add(Duration(milliseconds: ms)),
          );
      expect(observe(0).confirmations, 1);
      expect(observe(100).reason, NavigationMatchReason.repeatedFrame);
      expect(
        observe(2000, score: .6).reason,
        NavigationMatchReason.belowThreshold,
      );
      expect(observe(4000).confirmations, 1);
      expect(observe(26000).confirmations, 1);
      tracker.interrupt();
      expect(observe(28000).confirmations, 1);
      expect(observe(30000).accepted, isTrue);
      tracker.reset();
      expect(observe(32000).confirmations, 1);
      expect(
        observe(34000, score: double.nan).reason,
        NavigationMatchReason.invalid,
      );
    },
  );

  test(
    'slow mobile requests do not reset a valid critical confirmation after eight seconds',
    () {
      final tracker = NavigationWaypointTracker();
      final first = tracker.observe(
        targetSequence: 12,
        scores: {12: .86},
        threshold: .8,
        movementCount: 1,
        capturedAt: start,
      );
      expect(first.accepted, isFalse);
      final second = tracker.observe(
        targetSequence: 12,
        scores: {12: .85},
        threshold: .8,
        movementCount: 1,
        capturedAt: start.add(const Duration(seconds: 12)),
      );
      expect(second.accepted, isTrue);
      expect(second.requiredConfirmations, 2);
    },
  );

  test('nearby views share movement but weak images still cannot advance', () {
    final tracker = NavigationWaypointTracker();
    NavigationMatchDecision view(int target, int seconds, double score) =>
        tracker.observe(
          targetSequence: target,
          scores: {target: score},
          threshold: .7,
          movementCount: 1,
          capturedAt: start.add(Duration(seconds: seconds)),
        );
    expect(view(9, 0, .567).reason, NavigationMatchReason.belowThreshold);
    expect(view(9, 7, .819).accepted, isTrue);
    expect(view(10, 14, .95).accepted, isTrue);
    // Accepting a view does not restart the original 20-second allowance.
    expect(view(11, 21, .95).reason, NavigationMatchReason.waitingMovement);
  });

  test('one movement cannot consume a whole repetitive hallway', () {
    final tracker = NavigationWaypointTracker();
    var target = 1;
    for (var i = 0; i < 10; i++) {
      final d = tracker.observe(
        targetSequence: target,
        scores: {target: .9, target + 1: .92, target + 2: .95},
        forwardLimit: target + 2,
        threshold: .7,
        movementCount: 1,
        capturedAt: start.add(Duration(seconds: i)),
      );
      if (d.accepted) target = d.candidateSequence! + 1;
      if (i >= 2) expect(d.reason, NavigationMatchReason.waitingMovement);
    }
    expect(target, 5);
  });

  test(
    'arrival can confirm pre-turn, rotate in place and match outgoing views without another step',
    () {
      final tracker = NavigationWaypointTracker();
      final approaching = tracker.observe(
        targetSequence: 12,
        scores: {12: .81, 13: .83, 14: .9},
        forwardLimit: 14,
        threshold: .7,
        movementCount: 7,
        capturedAt: start,
      );
      expect(approaching.candidateSequence, 14);
      expect(approaching.accepted, isTrue);
      for (var i = 0; i < 2; i++) {
        final d = tracker.observe(
          targetSequence: 15,
          scores: {15: i == 0 ? .888 : .854, 16: .99},
          threshold: .8,
          movementCount: 7,
          capturedAt: start.add(Duration(seconds: 2 + i * 2)),
        );
        expect(d.accepted, i == 1);
        expect(d.candidateSequence, 15);
      }
      // Rotation supplies fresh physical evidence even after the arrival expires.
      for (var i = 0; i < 2; i++) {
        final d = tracker.observe(
          targetSequence: 16,
          scores: {16: .854, 17: .99},
          threshold: .8,
          movementCount: 7,
          turnInPlace: true,
          capturedAt: start.add(Duration(seconds: 30 + i * 2)),
        );
        expect(d.accepted, i == 1);
        expect(d.candidateSequence, 16);
      }
      final outgoing = tracker.observe(
        targetSequence: 17,
        scores: {17: .85},
        threshold: .7,
        movementCount: 7,
        capturedAt: start.add(const Duration(seconds: 34)),
      );
      expect(outgoing.accepted, isTrue);
    },
  );

  test(
    'recovery reset consumes historical movement instead of granting it again',
    () {
      final tracker = NavigationWaypointTracker()..reset(movementCount: 20);
      final d = tracker.observe(
        targetSequence: 3,
        scores: {3: .95},
        threshold: .7,
        movementCount: 20,
        capturedAt: start,
      );
      expect(d.reason, NavigationMatchReason.waitingMovement);
    },
  );
}
