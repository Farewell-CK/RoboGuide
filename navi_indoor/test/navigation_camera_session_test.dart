import 'dart:async';
import 'package:camera/camera.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/services/navigation_camera_session.dart';

class LifecycleCamera extends CameraController {
  LifecycleCamera({
    this.initializeGate,
    this.disposeGate,
    this.failCleanup = false,
  }) : super(
         const CameraDescription(
           name: 'lifecycle-test',
           lensDirection: CameraLensDirection.back,
           sensorOrientation: 0,
         ),
         ResolutionPreset.medium,
         enableAudio: false,
       );

  final Completer<void>? initializeGate;
  final Completer<void>? disposeGate;
  final bool failCleanup;
  final initialized = Completer<void>();
  final disposing = Completer<void>();
  int initializeCalls = 0;
  int disposeCalls = 0;

  @override
  Future<void> initialize() async {
    initializeCalls++;
    initialized.complete();
    if (initializeGate != null) await initializeGate!.future;
    value = value.copyWith(isInitialized: true);
  }

  @override
  Future<void> dispose() async {
    disposeCalls++;
    disposing.complete();
    if (disposeGate != null) await disposeGate!.future;
    if (failCleanup)
      throw CameraException('cleanup', 'platform cleanup failed');
  }
}

void main() {
  test('simultaneous resume requests initialize only one camera', () async {
    final gate = Completer<void>();
    final camera = LifecycleCamera(initializeGate: gate);
    var creations = 0;
    final session = NavigationCameraSession(() async {
      creations++;
      return camera;
    });
    final first = session.resume();
    final second = session.resume();
    await camera.initialized.future;
    expect(creations, 1);
    gate.complete();
    expect(await first, same(camera));
    expect(await second, same(camera));
    await session.close();
    expect(camera.disposeCalls, 1);
  });

  test(
    'background during initialization cannot publish the stale camera',
    () async {
      final gate = Completer<void>();
      final old = LifecycleCamera(initializeGate: gate);
      final fresh = LifecycleCamera();
      final cameras = [old, fresh].iterator;
      final session = NavigationCameraSession(() async {
        cameras.moveNext();
        return cameras.current;
      });
      final initial = session.resume();
      await old.initialized.future;
      await session.suspend();
      final resumed = session.resume();
      gate.complete();
      expect(await resumed, same(fresh));
      await initial;
      expect(session.controller, same(fresh));
      expect(old.disposeCalls, 1);
      expect(fresh.initializeCalls, 1);
      await session.close();
    },
  );

  test('slow old disposal cannot clear a replacement on resume', () async {
    final gate = Completer<void>();
    final old = LifecycleCamera(disposeGate: gate);
    final fresh = LifecycleCamera();
    var creations = 0;
    final session = NavigationCameraSession(
      () async => creations++ == 0 ? old : fresh,
    );
    await session.resume();
    final detachedGeneration = session.generation;
    final suspension = session.suspend();
    expect(session.controller, isNull);
    expect(session.generation, greaterThan(detachedGeneration));
    final secondSuspension = session.suspend();
    final resumed = session.resume();
    await old.disposing.future;
    expect(creations, 1);
    gate.complete();
    await suspension;
    await secondSuspension;
    expect(await resumed, same(fresh));
    expect(session.controller, same(fresh));
    expect(old.disposeCalls, 1);
    await session.close();
  });

  test(
    'leaving while permissions are pending releases the unused camera',
    () async {
      final factory = Completer<CameraController>();
      final enteredFactory = Completer<void>();
      final camera = LifecycleCamera();
      final session = NavigationCameraSession(() {
        enteredFactory.complete();
        return factory.future;
      });
      final pending = session.resume();
      await enteredFactory.future;
      await session.close();
      factory.complete(camera);
      expect(await pending, isNull);
      expect(camera.initializeCalls, 0);
      expect(camera.disposeCalls, 1);
      expect(await session.resume(), isNull);
    },
  );

  test(
    'platform cleanup errors do not prevent the next camera recovery',
    () async {
      final old = LifecycleCamera(failCleanup: true);
      final fresh = LifecycleCamera();
      var creations = 0;
      final session = NavigationCameraSession(
        () async => creations++ == 0 ? old : fresh,
      );
      await session.resume();
      await session.suspend();
      expect(await session.resume(), same(fresh));
      await session.close();
    },
  );
}
