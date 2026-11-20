import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:indoor_navigation_app/models/path_models.dart'
    show PathWaypoint, TurnType, InstructionType;
import 'package:indoor_navigation_app/services/dinov2_service.dart';
import 'package:indoor_navigation_app/services/position_localization_service.dart';
import 'package:indoor_navigation_app/services/real_time_navigation_service.dart';
import 'package:indoor_navigation_app/widgets/navigation_progress_panel.dart';
import 'package:indoor_navigation_app/widgets/navigation_arrival_panel.dart';
import 'package:indoor_navigation_app/services/navigation_motion.dart';

class FakeMotion extends NavigationMotion {
  @override
  Future<void> start() async {
    available = true;
    steps = 0;
  }

  @override
  Future<void> refresh() async {}
  @override
  Future<void> stop() async {
    available = false;
    steps = 0;
  }
}

class DeferredClip extends ClipService {
  DeferredClip() : super(serverUrl: 'http://example.test');
  final result = Completer<NavigationEmbeddingResult>();
  @override
  Future<NavigationEmbeddingResult> generateNavigationEmbeddingWithInpainting(
    File imageFile, {
    required double cleanSceneThreshold,
    required double peoplePresentThreshold,
    required double crowdedSceneThreshold,
    bool disableYolo = false,
  }) => result.future;
}

class MutableClip extends ClipService {
  MutableClip() : super(serverUrl: 'http://example.test');
  List<double> embedding = [1, ...List.filled(767, 0.0)];
  @override
  Future<NavigationEmbeddingResult> generateNavigationEmbeddingWithInpainting(
    File imageFile, {
    required double cleanSceneThreshold,
    required double peoplePresentThreshold,
    required double crowdedSceneThreshold,
    bool disableYolo = false,
  }) async => NavigationEmbeddingResult(
    embedding: embedding,
    peopleDetected: false,
    peopleCount: 0,
    confidenceScores: [],
    recommendedThreshold: .7,
  );
}

NavigationRoute route({
  List<List<double>>? embeddings,
  List<TurnType>? turns,
  List<double>? headings,
}) => NavigationRoute(
  pathId: 'test',
  pathName: '办公室到电梯口',
  startNodeId: 'office',
  endNodeId: 'lift',
  startNodeName: '办公室',
  endNodeName: '电梯口',
  estimatedDistance: 0,
  estimatedSteps: 0,
  estimatedDuration: Duration.zero,
  waypoints: List.generate(
    embeddings?.length ?? headings?.length ?? 13,
    (i) => PathWaypoint(
      id: '$i',
      embedding: embeddings?[i] ?? [1, ...List.filled(767, 0.0)],
      heading: headings?[i] ?? 115,
      headingChange: 0,
      turnType: turns?[i] ?? TurnType.straight,
      isDecisionPoint: false,
      sequenceNumber: i,
      timestamp: DateTime(2026),
    ),
  ),
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    await Supabase.initialize(
      url: 'https://example.supabase.co',
      anonKey: 'test-public-key',
      authOptions: FlutterAuthClientOptions(
        autoRefreshToken: false,
        detectSessionInUri: false,
        localStorage: EmptyLocalStorage(),
      ),
    );
  });
  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('flutter_tts'),
          (_) async => 1,
        );
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('hemanthraj/flutter_compass'),
          (_) async => null,
        );
  });
  tearDownAll(() => Supabase.instance.dispose());

  testWidgets(
    'guidance callback emits a turn only after visual confirmation and then arrival',
    (tester) async {
      final refs = List.generate(
        3,
        (i) => List.generate(768, (j) => i == j ? 1.0 : 0.0),
      );
      final clip = MutableClip()..embedding = refs[0];
      final motion = FakeMotion();
      final cues = <String>[];
      var now = DateTime(2026, 11, 10);
      final service = RealTimeNavigationService(
        clipService: clip,
        motion: motion,
        observationClock: () => now,
        onGuidance: cues.add,
      );
      final started = service.startNavigation(
        route(
          embeddings: refs,
          turns: [TurnType.straight, TurnType.left, TurnType.straight],
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await started;
      expect(cues.any((text) => text.contains('校准')), isTrue);
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      Future<void> frame() async {
        now = now.add(const Duration(seconds: 2));
        await service.processNavigationFrame(File('unused.jpg'));
      }

      await frame();
      await frame();
      clip.embedding = refs[1];
      motion.steps = 1;
      await frame();
      expect(cues.where((text) => text == '左转'), isEmpty);
      await frame();
      expect(cues.where((text) => text == '左转'), hasLength(1));
      clip.embedding = refs[2];
      motion.steps = 2;
      await frame();
      await frame();
      expect(service.state, NavigationState.destinationReached);
      expect(cues.last, contains('已到达电梯口'));
      await service.stopNavigation(silent: true);
    },
  );

  testWidgets(
    'final right-turn view immediately shows persistent arrival even when speech stop hangs',
    (tester) async {
      final refs = List.generate(
        3,
        (i) => List.generate(768, (j) => i == j ? 1.0 : 0.0),
      );
      final clip = MutableClip()..embedding = refs[0];
      final motion = FakeMotion();
      final spoken = <String>[];
      final instructions = <NavigationInstruction>[];
      final statuses = <String>[];
      final hangingStop = Completer<dynamic>();
      var holdStop = false;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(const MethodChannel('flutter_tts'), (
            call,
          ) async {
            if (call.method == 'speak') spoken.add(call.arguments.toString());
            if (holdStop && call.method == 'stop') return hangingStop.future;
            return 1;
          });
      var now = DateTime(2026, 11, 5);
      final service = RealTimeNavigationService(
        clipService: clip,
        motion: motion,
        observationClock: () => now,
        onInstructionUpdate: instructions.add,
        onStatusUpdate: statuses.add,
      );
      final started = service.startNavigation(
        route(
          embeddings: refs,
          turns: [TurnType.straight, TurnType.straight, TurnType.right],
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await started;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      Future<void> frame() async {
        now = now.add(const Duration(seconds: 2));
        await service.processNavigationFrame(File('unused.jpg'));
      }

      await frame();
      await frame();
      clip.embedding = refs[1];
      motion.steps = 1;
      await frame();
      await frame();
      clip.embedding = refs[2];
      motion.steps = 2;
      await frame();
      expect(service.state, NavigationState.navigating);
      holdStop = true;
      final completed = frame();
      await tester.pump();
      expect(service.state, NavigationState.destinationReached);
      expect(service.currentWaypointIndex, 3);
      expect(service.progressPercentage, 100);
      expect(service.currentRoute!.endNodeName, '电梯口');
      expect(service.matchingStatus, contains('已到达电梯口'));
      expect(instructions.last.type, InstructionType.arrive);
      expect(instructions.any((i) => i.displayText == '右转'), isFalse);
      expect(statuses.last, contains('本次导航完成'));
      await tester.pump(const Duration(milliseconds: 501));
      await completed;
      expect(spoken.last, contains('已到达电梯口'));
      await tester.pump(const Duration(seconds: 30));
      await frame();
      expect(service.state, NavigationState.destinationReached);
      expect(service.progressPercentage, 100);
      expect(
        instructions.where((i) => i.type == InstructionType.arrive),
        hasLength(1),
      );
      hangingStop.complete(1);
      await service.stopNavigation(silent: true);
      expect(service.state, NavigationState.idle);
      expect(service.currentRoute, isNull);
    },
  );

  testWidgets(
    'arrival panel names destination and shows completed reference count',
    (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: NavigationArrivalPanel(destination: '电梯口', total: 27),
          ),
        ),
      );
      expect(find.text('已到达电梯口'), findsOneWidget);
      expect(find.text('路线已完成 · 27 / 27 · 100%'), findsOneWidget);
      expect(find.text('本次导航结束，请停下。'), findsOneWidget);
      await tester.pump(const Duration(seconds: 30));
      expect(find.text('已到达电梯口'), findsOneWidget);
    },
  );

  testWidgets(
    'navigation protects a missed recorded turn without changing the source route',
    (tester) async {
      final source = route(
        headings: [122.1885, 121.8999, 115.0495, 55.0195, 35.4857, 32.8421],
      );
      final service = RealTimeNavigationService(clipService: MutableClip());
      final started = service.startNavigation(source);
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await started;
      expect(service.currentRoute!.waypoints[3].turnType, TurnType.left);
      expect(source.waypoints[3].turnType, TurnType.straight);
      await service.stopNavigation(silent: true);
    },
  );

  testWidgets(
    'arrival confirmation is a visible tappable fallback in the progress panel',
    (tester) async {
      var confirmed = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: NavigationProgressPanel(
              completed: 15,
              total: 27,
              instruction: '接近左转弯处',
              status: '请确认已到达此处',
              onConfirmMovement: () => confirmed++,
            ),
          ),
        ),
      );
      final button = find.text('确认已到达此处，重新识别');
      expect(button, findsOneWidget);
      await tester.tap(button);
      expect(confirmed, 1);
      expect(find.text('路线跟踪 15 / 27 · 56%'), findsOneWidget);
    },
  );

  testWidgets(
    'manual recovery cannot bypass confirmation by jumping to a future point',
    (tester) async {
      final references = <List<double>>[
        [1, ...List.filled(767, 0.0)],
        [.8, .6, ...List.filled(766, 0.0)],
        [0, 0, 1, ...List.filled(765, 0.0)],
      ];
      final clip = MutableClip()..embedding = references[2];
      var frameTime = DateTime(2026, 11, 4);
      final service = RealTimeNavigationService(
        clipService: clip,
        observationClock: () => frameTime,
      );
      final start = service.startNavigation(route(embeddings: references));
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await start;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      for (var i = 0; i < 29; i++) {
        frameTime = frameTime.add(const Duration(seconds: 1));
        await service.processNavigationFrame(File('unused.jpg'));
      }
      frameTime = frameTime.add(const Duration(seconds: 1));
      final failure = service.processNavigationFrame(File('unused.jpg'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 200));
      await failure;
      expect(service.state, NavigationState.manualCaptureInProgress);
      await service.processManualCapturedFrame(File('recovery-fixture-1.jpg'));
      await service.processManualCapturedFrame(File('recovery-fixture-2.jpg'));
      await tester.runAsync(
        () =>
            service.processManualCapturedFrame(File('recovery-fixture-3.jpg')),
      );
      expect(service.state, NavigationState.navigating);
      expect(service.currentWaypointIndex, 0);
      expect(service.progressPercentage, 0);
      expect(service.matchingStatus, contains('不跳过未确认采样点'));
      await service.stopNavigation(silent: true);
      // Let bounded, nonblocking speech callbacks settle after real file IO.
      await tester.pump(const Duration(seconds: 3));
      expect(service.state, NavigationState.idle);
    },
  );

  testWidgets(
    'real service confirms anchor twice and straight views once with new steps',
    (tester) async {
      final clip = MutableClip();
      final sample = route();
      final references = <List<double>>[
        [1, ...List.filled(767, 0.0)],
        [.8, .6, ...List.filled(766, 0.0)],
        [0, 0, 1, ...List.filled(765, 0.0)],
      ];
      final navigationRoute = NavigationRoute(
        pathId: sample.pathId,
        pathName: sample.pathName,
        startNodeId: sample.startNodeId,
        endNodeId: sample.endNodeId,
        startNodeName: sample.startNodeName,
        endNodeName: sample.endNodeName,
        estimatedDistance: 0,
        estimatedSteps: 0,
        estimatedDuration: Duration.zero,
        waypoints: List.generate(
          3,
          (i) => PathWaypoint(
            id: '$i',
            embedding: references[i],
            heading: 115,
            headingChange: 0,
            turnType: TurnType.straight,
            isDecisionPoint: false,
            sequenceNumber: i,
            timestamp: DateTime(2026),
          ),
        ),
      );
      var frameTime = DateTime(2026, 11, 4);
      final motion = FakeMotion();
      final service = RealTimeNavigationService(
        clipService: clip,
        motion: motion,
        observationClock: () => frameTime,
      );
      final start = service.startNavigation(navigationRoute);
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await start;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      Future<void> frame() async {
        await tester.pump(const Duration(seconds: 1));
        frameTime = frameTime.add(const Duration(seconds: 1));
        await service.processNavigationFrame(File('unused.jpg'));
      }

      await frame();
      expect(service.currentWaypointIndex, 0);
      await frame();
      expect(service.currentWaypointIndex, 1);
      // This view also scores .8 against point two, above the old .7 gate.
      await frame();
      await frame();
      expect(service.currentWaypointIndex, 1);
      expect(service.matchingStatus, contains('到达此处'));
      // Fallback is offered even with a working but temporarily quiet sensor.
      expect(service.needsManualMovement, isTrue);
      motion.steps++;
      clip.embedding = references[1];
      await frame();
      expect(service.currentWaypointIndex, 2);
      await frame();
      expect(service.currentWaypointIndex, 2);
      await frame();
      expect(service.currentWaypointIndex, 2);
      await service.stopNavigation(silent: true);
    },
  );

  testWidgets(
    'manual movement fallback never forces a bad visual match or grants unlimited progress',
    (tester) async {
      final clip = MutableClip();
      var now = DateTime(2026, 11, 4);
      final service = RealTimeNavigationService(
        clipService: clip,
        observationClock: () => now,
      );
      final started = service.startNavigation(route());
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await started;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      Future<void> frame() async {
        now = now.add(const Duration(seconds: 1));
        await service.processNavigationFrame(File('unused.jpg'));
      }

      await frame();
      await frame();
      expect(service.currentWaypointIndex, 1);
      expect(service.needsManualMovement, isTrue);
      service.confirmMovement();
      service.confirmMovement();
      clip.embedding = [0, 1, ...List.filled(766, 0.0)];
      await frame();
      await frame();
      expect(service.currentWaypointIndex, 1);
      clip.embedding = [1, ...List.filled(767, 0.0)];
      await frame();
      expect(service.currentWaypointIndex, 2);
      await frame();
      expect(service.currentWaypointIndex, 3);
      await frame();
      await frame();
      expect(service.currentWaypointIndex, 5);
      await frame();
      expect(service.currentWaypointIndex, 5);
      await service.stopNavigation(silent: true);
      await tester.pump(const Duration(seconds: 3));
    },
  );

  testWidgets(
    'turn prompt waits for its own confirmed view and remains visible afterwards',
    (tester) async {
      final refs = List.generate(
        4,
        (i) => List.generate(768, (j) => i == j ? 1.0 : 0.0),
      );
      final clip = MutableClip()..embedding = refs[0];
      final motion = FakeMotion();
      final instructions = <NavigationInstruction>[];
      var now = DateTime(2026, 11, 4);
      final service = RealTimeNavigationService(
        clipService: clip,
        motion: motion,
        observationClock: () => now,
        onInstructionUpdate: instructions.add,
      );
      final started = service.startNavigation(
        route(
          embeddings: refs,
          turns: [
            TurnType.straight,
            TurnType.straight,
            TurnType.left,
            TurnType.straight,
          ],
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await started;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      Future<void> confirm(int seq) async {
        clip.embedding = refs[seq];
        motion.steps = seq;
        for (var i = 0; i < 2; i++) {
          now = now.add(const Duration(seconds: 1));
          await service.processNavigationFrame(File('unused.jpg'));
        }
      }

      await confirm(0);
      await confirm(1);
      expect(service.currentWaypointIndex, 2);
      expect(instructions.any((i) => i.displayText == '左转'), isFalse);
      expect(instructions.last.displayText, contains('接近转弯'));
      await confirm(2);
      expect(service.currentWaypointIndex, 3);
      expect(instructions.last.displayText, '左转');
      await service.stopNavigation(silent: true);
      await tester.pump(const Duration(seconds: 3));
    },
  );

  testWidgets(
    'rotation at a corner confirms without another step or asking for a second turn',
    (tester) async {
      final refs = List.generate(
        4,
        (i) => List.generate(768, (j) => i == j ? 1.0 : 0.0),
      );
      final clip = MutableClip()..embedding = refs[0];
      final motion = FakeMotion();
      final instructions = <NavigationInstruction>[];
      var now = DateTime(2026, 11, 5);
      final service = RealTimeNavigationService(
        clipService: clip,
        motion: motion,
        observationClock: () => now,
        onInstructionUpdate: instructions.add,
      );
      final started = service.startNavigation(
        route(
          embeddings: refs,
          headings: [115, 115, 55, 35],
          turns: [
            TurnType.straight,
            TurnType.straight,
            TurnType.left,
            TurnType.straight,
          ],
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await started;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      Future<void> frame() async {
        now = now.add(const Duration(seconds: 2));
        await service.processNavigationFrame(File('unused.jpg'));
      }

      await frame();
      await frame();
      motion.steps = 1;
      clip.embedding = refs[1];
      await frame();
      await frame();
      expect(service.currentWaypointIndex, 2);
      // The arrival allowance has expired; physical rotation is the evidence.
      now = now.add(const Duration(seconds: 30));
      final messenger =
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      messenger.handlePlatformMessage(
        'hemanthraj/flutter_compass',
        const StandardMethodCodec().encodeSuccessEnvelope(<double>[55, 55, 3]),
        (_) {},
      );
      await tester.pump();
      expect(service.currentHeading, 55);
      clip.embedding = refs[2];
      await frame();
      expect(service.currentWaypointIndex, 2);
      await frame();
      expect(service.currentWaypointIndex, 3);
      expect(motion.steps, 1);
      expect(instructions.last.displayText, '左转已确认，沿当前方向继续');
      expect(instructions.last.type, InstructionType.continue_);
      await service.stopNavigation(silent: true);
      await tester.pump(const Duration(seconds: 3));
    },
  );

  testWidgets(
    'late response from stopped navigation cannot advance a restarted route',
    (tester) async {
      final clip = DeferredClip();
      final instructions = <NavigationInstruction>[];
      final service = RealTimeNavigationService(
        clipService: clip,
        onInstructionUpdate: instructions.add,
      );
      final start = service.startNavigation(route());
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await start;
      await service.skipOrientationAndStartNavigation();
      await tester.pump(const Duration(seconds: 2));
      expect(service.state, NavigationState.navigating);
      final processing = service.processNavigationFrame(File('unused.jpg'));
      await service.stopNavigation(silent: true);
      instructions.clear();
      final restarted = service.startNavigation(route());
      await tester.pump();
      clip.result.complete(
        NavigationEmbeddingResult(
          embedding: [1, ...List.filled(767, 0.0)],
          peopleDetected: false,
          peopleCount: 0,
          confidenceScores: [],
          recommendedThreshold: .7,
        ),
      );
      await processing;
      expect(service.currentWaypointIndex, 0);
      expect(service.progressPercentage, 0);
      expect(service.state, NavigationState.initialOrientation);
      expect(instructions, isEmpty);
      await tester.pump(const Duration(seconds: 3));
      await restarted;
      await service.stopNavigation(silent: true);
    },
  );

  testWidgets(
    'stopping during orientation confirmation cancels delayed navigation start',
    (tester) async {
      final service = RealTimeNavigationService(clipService: DeferredClip());
      final start = service.startNavigation(route());
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      await start;
      await service.skipOrientationAndStartNavigation();
      await service.stopNavigation(silent: true);
      await tester.pump(const Duration(seconds: 4));
      expect(service.state, NavigationState.idle);
      expect(service.currentRoute, isNull);
    },
  );

  testWidgets(
    'progress explains next target and rebuilds from the same confirmed count on restart',
    (tester) async {
      Future<void> show(int completed) => tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: NavigationProgressPanel(
              completed: completed,
              total: 13,
              status: '相似度 0.615 / 门槛 0.70',
              instruction: '继续直行',
            ),
          ),
        ),
      );
      await show(5);
      expect(find.text('路线跟踪 5 / 13 · 38%'), findsOneWidget);
      expect(find.text('下一参考点：第 6 个'), findsOneWidget);
      await show(0);
      expect(find.text('路线跟踪 0 / 13 · 0%'), findsOneWidget);
      expect(find.text('下一参考点：第 1 个'), findsOneWidget);
      expect(find.text('下一参考点：第 6 个'), findsNothing);
    },
  );
}
