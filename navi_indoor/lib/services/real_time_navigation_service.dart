import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:flutter_compass/flutter_compass.dart';
import '../services/dinov2_service.dart';
import '../services/position_localization_service.dart';
import '../services/supabase_service.dart';
import '../models/path_models.dart';
import '../models/orientation_math.dart';
import '../models/navigation_match_policy.dart';
import '../models/navigation_turns.dart';
import 'navigation_feedback.dart';
import 'navigation_diagnostics.dart';
import 'navigation_motion.dart';

enum NavigationState {
  idle,
  initialOrientation,
  navigating,
  approachingWaypoint,
  reorientingUser,
  destinationReached,
  awaitingManualCapture,
  manualCaptureInProgress,
  analyzingCapturedFrames,
  recoveryFailed,
}

class RealTimeNavigationService {
  final ClipService _clipService;
  late final FlutterTts _tts = FlutterTts();
  final DateTime Function() _observationClock;
  final NavigationMotion _motion;
  int _manualMovement = 0;
  bool _waitingForPositionConfirmation = false;
  final SupabaseService _supabaseService = SupabaseService();

  // Navigation state
  NavigationRoute? _currentRoute;
  NavigationState _state = NavigationState.idle;
  int _currentWaypointIndex = 0;
  int _currentSequenceNumber = 0;
  Timer? _navigationTimer;
  Timer? _orientationTransitionTimer;
  int _sessionGeneration = 0;
  bool _orientationTransitionPending = false;
  final NavigationDiagnostics _diagnostics = NavigationDiagnostics();
  final NavigationWaypointTracker _waypointTracker =
      NavigationWaypointTracker();
  String _matchingStatus = '等待路线画面匹配';
  StreamSubscription<CompassEvent>? _compassSubscription;

  // Tracking
  double? _currentHeading;
  List<double>? _lastCapturedEmbedding;
  DateTime _lastGuidanceTime = DateTime.now();
  DateTime? _lastVisualMatchTime;

  // YOLO toggle state
  bool _disableYolo = false;

  // To change any threshold value, modify these constants only
  static const double _waypointReachedThresholdDefault =
      navigationWaypointThreshold;
  static const double _cleanSceneThreshold = navigationWaypointThreshold;
  static const double _peoplePresentThreshold = navigationWaypointThreshold;
  static const double _crowdedSceneThreshold = navigationWaypointThreshold;
  static const Duration _guidanceInterval = Duration(seconds: 1);
  static const Duration _repositioningTimeout = Duration(
    seconds: 10,
  ); // Dynamic threshold and audio management
  double _currentWaypointThreshold = navigationWaypointThreshold;
  String? _lastSpokenInstruction;
  DateTime? _lastInstructionTime;

  // Waypoint recovery system
  int _consecutiveWaypointFailures = 0;
  static const int _waypointFailureThreshold = 30;
  static const double _recoveryThreshold = 0.76;
  int _manualCaptureCount = 0;
  List<File> _capturedRecoveryFrames = [];
  int _recoveryAttempts = 0;
  static const int _maxRecoveryAttempts = 3;

  // Callbacks
  final Function(NavigationState state)? onStateChanged;
  final Function(String message)? onStatusUpdate;
  final Function(NavigationInstruction instruction)? onInstructionUpdate;
  final Function(String error)? onError;
  final void Function(String message)? onGuidance;
  final Function(String debugInfo)? onDebugUpdate;
  final Function()? onRequestManualCapture;
  final Function(File imageFile)? onManualFrameCaptured;

  RealTimeNavigationService({
    required ClipService clipService,
    DateTime Function()? observationClock,
    NavigationMotion? motion,
    this.onStateChanged,
    this.onStatusUpdate,
    this.onInstructionUpdate,
    this.onError,
    this.onGuidance,
    this.onDebugUpdate,
    this.onRequestManualCapture,
    this.onManualFrameCaptured,
  }) : _clipService = clipService,
       _observationClock = observationClock ?? DateTime.now,
       _motion = motion ?? NavigationMotion() {
    if (onGuidance == null) _initializeTTS();
  }

  // Public getters
  NavigationState get state => _state;
  NavigationRoute? get currentRoute => _currentRoute;
  int get currentWaypointIndex => _currentSequenceNumber;
  int get sessionGeneration => _sessionGeneration;
  String get matchingStatus => _matchingStatus;
  bool get needsManualMovement =>
      (!_motion.available || _waitingForPositionConfirmation) &&
      _state == NavigationState.navigating &&
      _currentSequenceNumber > 0;
  String get motionHint =>
      _waitingForPositionConfirmation
          ? '若已走到此处，可停在匹配位置点击确认，无需为刷新计步离开这里。'
          : _motion.available
          ? '计步辅助：${_motion.steps} 步；邻近参考点可共用近期移动，转弯可原地确认。'
          : '计步不可用；到达参考位置后点击确认，仍需画面匹配。';
  void confirmMovement() {
    if (!needsManualMovement) return;
    _manualMovement++;
    _waitingForPositionConfirmation = false;
    _waypointTracker.interrupt();
    _diagnostics.add({'type': 'manual_movement', 'count': _manualMovement});
    _matchingStatus = '已记录到位确认，保持当前画面，正在重新识别';
    onStatusUpdate?.call(_matchingStatus);
  }

  double get progressPercentage =>
      _currentRoute != null
          ? (_currentSequenceNumber / _currentRoute!.waypoints.length) * 100
          : 0.0;

  /// Get the current compass heading (null if not available)
  double? get currentHeading => _currentHeading;

  /// Set whether YOLO detection should be disabled
  void setDisableYolo(bool disable) {
    _disableYolo = disable;
    print('🎯 YOLO Detection: ${_disableYolo ? 'DISABLED' : 'ENABLED'}');
  }

  /// Get the target heading for initial orientation (null if not in orientation phase)
  double? get targetHeading {
    if (_state != NavigationState.initialOrientation || _currentRoute == null) {
      return null;
    }
    final firstWaypoint = _getWaypointBySequence(0);
    return firstWaypoint?.heading;
  }

  /// Update debug display with navigation information
  void _updateDebugDisplay(String debugInfo) {
    onDebugUpdate?.call(debugInfo);
  }

  Future<void> startNavigation(NavigationRoute route) async {
    if (_state == NavigationState.navigating ||
        _state == NavigationState.initialOrientation) {
      onError?.call('Navigation already in progress');
      return;
    }

    final originalWaypoints = route.waypoints;
    final trackedWaypoints = navigationWaypointsWithTurns(originalWaypoints);
    final inferredSequences = <int>[
      for (final point in trackedWaypoints)
        if (originalWaypoints
                .firstWhere((w) => w.sequenceNumber == point.sequenceNumber)
                .turnType !=
            point.turnType)
          point.sequenceNumber,
    ];
    route = NavigationRoute(
      pathId: route.pathId,
      pathName: route.pathName,
      startNodeId: route.startNodeId,
      endNodeId: route.endNodeId,
      startNodeName: route.startNodeName,
      endNodeName: route.endNodeName,
      estimatedDistance: route.estimatedDistance,
      estimatedSteps: route.estimatedSteps,
      estimatedDuration: route.estimatedDuration,
      waypoints: trackedWaypoints,
    );
    final generation = ++_sessionGeneration;
    _waypointTracker.reset();
    _manualMovement = 0;
    _waitingForPositionConfirmation = false;
    _orientationTransitionTimer?.cancel();
    _orientationTransitionPending = false;
    _diagnostics.start(_supabaseService.client.auth.currentUser?.id, {
      'name': route.pathName,
      'destination': route.endNodeName,
      'inferred_turn_sequences': inferredSequences,
      'waypoints': [
        for (final point in route.waypoints)
          {
            'sequence': point.sequenceNumber,
            'heading': point.heading,
            'turn': point.turnType.name,
            'dimensions': point.embedding.length,
          },
      ],
    });
    _matchingStatus = '等待路线画面匹配';
    try {
      _currentRoute = route;
      _currentWaypointIndex = 0;
      _currentSequenceNumber = 0;

      // Reset audio tracking for new navigation session
      _lastSpokenInstruction = null;
      _lastInstructionTime = null;
      _lastVisualMatchTime = null; // Reset visual match tracking
      _currentWaypointThreshold = _waypointReachedThresholdDefault;
      _consecutiveWaypointFailures = 0;
      _recoveryAttempts = 0;

      await _cleanupRecoveryFrames();
      if (generation != _sessionGeneration) return;

      // START WITH INITIAL ORIENTATION instead of direct navigation
      _setState(NavigationState.initialOrientation);

      print('🚀 Navigation started - Starting orientation phase');
      print('⏳ Waiting for compass reading to guide initial direction...');

      _updateDebugDisplay(
        '🚀 NAVIGATION STARTED\n🧭 ORIENTATION PHASE\n⏳ Waiting for compass...\n🎯 Destination: ${route.endNodeName}',
      );

      onStatusUpdate?.call('正在导航至 ${route.endNodeName}');
      await _speak('导航已启动，请先对准行进方向。');
      if (generation != _sessionGeneration) return;

      onStatusUpdate?.call('正在启用步行检测，仅用于防止原地跳点');
      await _motion.start();
      if (generation != _sessionGeneration) return;
      _diagnostics.add({'type': 'motion', 'available': _motion.available});
      // Initialize compass
      await _initializeCompass();
      if (generation != _sessionGeneration) return;

      // Start orientation guidance instead of navigation
      await _startInitialOrientation();
    } catch (e) {
      onError?.call('Failed to start navigation: $e');
    }
  }

  /// Stop navigation
  Future<void> stopNavigation({bool silent = false}) async {
    final generation = ++_sessionGeneration;
    _waypointTracker.reset();
    final hadRoute = _currentRoute != null;
    _orientationTransitionTimer?.cancel();
    _orientationTransitionPending = false;
    _diagnostics.add({'type': 'stop', 'confirmed': _currentSequenceNumber});
    // Invalidate frames before any await, including those from the same route.
    _currentRoute = null;
    _waitingForPositionConfirmation = false;
    _currentSequenceNumber = 0;
    _matchingStatus = '等待路线画面匹配';
    _setState(NavigationState.idle);
    _navigationTimer?.cancel();
    _compassSubscription?.cancel();
    unawaited(_motion.stop());

    if (hadRoute && !silent) {
      await _speak('导航已停止。');
    }
    if (generation != _sessionGeneration) return;

    _currentRoute = null;
    _currentWaypointIndex = 0;
    _currentSequenceNumber = 0;

    // Reset audio tracking and people detection state
    _lastSpokenInstruction = null;
    _lastInstructionTime = null;
    _lastVisualMatchTime = null;
    _currentWaypointThreshold = _waypointReachedThresholdDefault;

    // Reset all failure counters
    _consecutiveWaypointFailures = 0;
    _recoveryAttempts = 0;

    // Clean up recovery data
    await _cleanupRecoveryFrames();
    if (generation != _sessionGeneration) return;

    _setState(NavigationState.idle);

    onStatusUpdate?.call('导航已停止');
  }

  /// 🧭 Start initial orientation phase - guide user to face the first waypoint direction
  Future<void> _startInitialOrientation() async {
    final generation = _sessionGeneration;
    if (_currentRoute == null || _currentRoute!.waypoints.isEmpty) {
      onError?.call('No route or waypoints available for orientation');
      return;
    }

    // Get the first waypoint (sequence 0)
    final firstWaypoint = _getWaypointBySequence(0);
    if (firstWaypoint == null) {
      onError?.call('Cannot find first waypoint for orientation');
      return;
    }

    print('🧭 Starting initial orientation phase');
    print(
      '🎯 Target heading for first waypoint: ${firstWaypoint.heading.toStringAsFixed(1)}°',
    );

    _updateDebugDisplay(
      '🧭 INITIAL ORIENTATION\n🎯 Target: ${firstWaypoint.heading.toStringAsFixed(1)}°\n⏳ Waiting for your compass reading...',
    );

    // Audio feedback for orientation start
    await _speak('开始校准朝向，请面向路线的出发方向。');
    if (generation != _sessionGeneration) return;
    onStatusUpdate?.call('正在校准朝向，请对准出发方向');

    await _speak('请缓慢转动身体，听到提示后停下。');
    if (generation != _sessionGeneration) return;
    onStatusUpdate?.call('请缓慢转动，寻找正确朝向');

    // Update debug display to show waiting period
    _updateDebugDisplay(
      '🧭 GETTING READY\n⏳ Please wait while I prepare orientation...\n🎯 Keep your phone steady',
    );
    onStatusUpdate?.call('正在准备朝向校准…');

    // Wait a few seconds for user to process the initial instructions before starting orientation checking
    await Future.delayed(Duration(seconds: 3));
    if (generation != _sessionGeneration ||
        _state != NavigationState.initialOrientation)
      return;

    // Start periodic orientation checking
    _navigationTimer = Timer.periodic(
      Duration(seconds: 1),
      (_) => _checkOrientation(),
    );
  }

  /// 🧭 Check if user is facing the correct direction for the first waypoint
  Future<void> _checkOrientation() async {
    if (_state != NavigationState.initialOrientation || _currentRoute == null)
      return;

    // Need compass reading
    if (_currentHeading == null) {
      print('⏳ Waiting for compass reading...');
      _updateDebugDisplay(
        '🧭 ORIENTATION GUIDANCE:\n⏳ Waiting for compass reading...\n📱 Please ensure location services are enabled',
      );
      return;
    }

    final firstWaypoint = _getWaypointBySequence(0);
    if (firstWaypoint == null) {
      print('❌ No first waypoint found');
      return;
    }

    final targetHeading = firstWaypoint.heading;
    final currentHeading = _currentHeading!;

    // Calculate the shortest angular difference
    final headingDifference = _calculateHeadingDifference(
      currentHeading,
      targetHeading,
    );
    final absoluteDifference = headingDifference.abs();

    // Enhanced debugging
    print('🧭 ORIENTATION CHECK:');
    print('   📍 Current: ${currentHeading.toStringAsFixed(1)}°');
    print('   🎯 Target: ${targetHeading.toStringAsFixed(1)}°');
    print(
      '   ↔️ Difference: ${headingDifference.toStringAsFixed(1)}° (abs: ${absoluteDifference.toStringAsFixed(1)}°)',
    );
    print('   ✅ Within tolerance (≤5°): ${absoluteDifference <= 5.0}');

    // Update debug display with real-time orientation info
    _updateDebugDisplay('''
      🧭 ORIENTATION GUIDANCE:
      ━━━━━━━━━━━━━━━━━━━━
      📍 Current: ${currentHeading.toStringAsFixed(1)}°
      🎯 Target: ${targetHeading.toStringAsFixed(1)}°
      ↔️ Raw diff: ${headingDifference.toStringAsFixed(1)}°
      📐 Abs diff: ${absoluteDifference.toStringAsFixed(1)}°
      ${_getOrientationStatus(absoluteDifference)}
      ━━━━━━━━━━━━━━━━━━━━
    ''');

    // Use the same tolerance as the screen; do not keep turning after alignment.
    if (isDepartureHeadingAligned(currentHeading, targetHeading)) {
      print(
        '✅ User is facing the correct direction! (diff: ${absoluteDifference.toStringAsFixed(1)}°)',
      );
      await _orientationComplete();
    } else {
      // Provide turning guidance
      print(
        '🔄 Need adjustment: ${headingDifference > 0 ? "turn right" : "turn left"} by ${absoluteDifference.toStringAsFixed(1)}°',
      );
      await _provideOrientationGuidance(headingDifference, absoluteDifference);
    }
  }

  /// Calculate the shortest angular difference between current and target heading
  double _calculateHeadingDifference(double current, double target) {
    // Ensure both headings are normalized [0, 360)
    current = _normalizeHeading(current);
    target = _normalizeHeading(target);

    double difference = relativeTargetHeading(current, target);

    // Normalize to [-180, 180] range for shortest path
    if (difference > 180) {
      difference -= 360;
    } else if (difference < -180) {
      difference += 360;
    }

    // Debug the calculation
    print('🧮 HEADING CALCULATION:');
    print('   Current (normalized): ${current.toStringAsFixed(1)}°');
    print('   Target (normalized): ${target.toStringAsFixed(1)}°');
    print('   Raw difference: ${(target - current).toStringAsFixed(1)}°');
    print('   Normalized difference: ${difference.toStringAsFixed(1)}°');

    return difference;
  }

  double _normalizeHeading(double heading) {
    return normalizeNavigationHeading(heading);
  }

  /// Get user-friendly orientation status text
  String _getOrientationStatus(double absoluteDifference) {
    if (absoluteDifference <= departureHeadingTolerance) {
      return '✅ PERFECT! Ready to start (${absoluteDifference.toStringAsFixed(1)}° off)';
    } else if (absoluteDifference <= 15) {
      return '🔄 CLOSE - keep adjusting (${absoluteDifference.toStringAsFixed(1)}° off)';
    } else if (absoluteDifference <= 45) {
      return '🔄 TURNING - keep going (${absoluteDifference.toStringAsFixed(1)}° off)';
    } else {
      return '🔄 MAJOR TURN - continue turning (${absoluteDifference.toStringAsFixed(1)}° off)';
    }
  }

  /// Provide audio guidance for orientation
  Future<void> _provideOrientationGuidance(
    double headingDifference,
    double absoluteDifference,
  ) async {
    String guidance;

    if (absoluteDifference > 90) {
      // Large turn needed
      if (headingDifference > 0) {
        guidance = '请继续向右转';
      } else {
        guidance = '请继续向左转';
      }
    } else if (absoluteDifference > 45) {
      // Medium turn
      if (headingDifference > 0) {
        guidance = '右转';
      } else {
        guidance = '左转';
      }
    } else if (absoluteDifference > 5) {
      // Fine adjustment
      if (headingDifference > 0) {
        guidance = '请稍微向右转';
      } else {
        guidance = '请稍微向左转';
      }
    } else {
      return; // Already handled in _checkOrientation
    }

    // Use smart speaking to avoid repetition
    await _speakSmart(guidance);
    onStatusUpdate?.call(guidance);
  }

  /// Called when user is correctly oriented - transition to actual navigation
  Future<void> _orientationComplete() async {
    if (_state != NavigationState.initialOrientation ||
        _orientationTransitionPending)
      return;
    final generation = _sessionGeneration;
    _orientationTransitionPending = true;
    _navigationTimer?.cancel(); // Stop orientation timer

    // First, tell the user they're correctly oriented and wait
    await _speak('朝向正确，请保持这个方向。');
    if (generation != _sessionGeneration) return;
    onStatusUpdate?.call('朝向已确认，正在准备导航');

    await _speak('即将开始导航，请保持当前朝向。');
    if (generation != _sessionGeneration) return;
    onStatusUpdate?.call('正在准备导航…');

    print('✅ User is correctly oriented - confirming before navigation');
    _updateDebugDisplay(
      '✅ ORIENTATION COMPLETE\n⏳ Preparing navigation...\n� Getting ready to start...',
    );

    // Give user a moment to process the confirmation, then start navigation
    if (generation != _sessionGeneration) return;
    _orientationTransitionTimer = Timer(Duration(seconds: 2), () async {
      if (generation != _sessionGeneration ||
          _state != NavigationState.initialOrientation)
        return;
      _orientationTransitionPending = false;
      _setState(NavigationState.navigating);

      await _speak('开始导航。');
      if (generation != _sessionGeneration) return;
      onStatusUpdate?.call('导航已开始');

      print('🚀 Starting actual navigation after orientation confirmation');
      _updateDebugDisplay('''
🚀 NAVIGATION STARTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Orientation complete - ready for waypoint guidance
🎯 正在寻找第 1 个路线采样点
⏳ 请沿已知路线慢行，识别进度不是加载进度
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
''');

      // Reset guidance time to prevent immediate guidance
      _lastGuidanceTime = DateTime.now();

      // DON'T provide waypoint instruction during orientation
      // Wait for first visual match before providing navigation guidance
      // Only compass guidance should be given during orientation phase

      // Start periodic navigation guidance
      _navigationTimer = Timer.periodic(
        _guidanceInterval,
        (_) => _checkProgress(),
      );

      // Provide initial guidance for the first waypoint
      final firstWaypoint = _getWaypointBySequence(0);
      if (firstWaypoint != null) {
        await _provideGuidance(firstWaypoint, 0.5);
      }
    });
  }

  /// Allow user to skip orientation if they're confident about their direction
  ///
  /// This can only be called during the initialOrientation state.
  /// Use this if the user already knows which direction to face.
  Future<void> skipOrientationAndStartNavigation() async {
    if (_state != NavigationState.initialOrientation) {
      onError?.call('Can only skip orientation during orientation phase');
      return;
    }

    print('⏩ User chose to skip orientation - starting navigation directly');
    await _speak('跳过朝向校准，开始导航。');

    await _orientationComplete();
  }

  /// Process current camera frame for navigation guidance
  Future<void> processNavigationFrame(File imageFile, {int? captureMs}) async {
    // Only process frames during actual navigation, not during orientation
    if (_state != NavigationState.navigating || _currentRoute == null) return;
    final generation = _sessionGeneration;
    final sequence = _currentSequenceNumber;
    final route = _currentRoute!;
    final started = _observationClock();

    try {
      await _motion.refresh();
      if (generation != _sessionGeneration ||
          sequence != _currentSequenceNumber ||
          _state != NavigationState.navigating)
        return;
      final movement = _motion.steps + _manualMovement;
      final motionMs = _observationClock().difference(started).inMilliseconds;
      // Get current target waypoint by SEQUENCE NUMBER first to access people_detected info
      final targetWaypoint = _getWaypointBySequence(_currentSequenceNumber);
      if (targetWaypoint == null) {
        print('No waypoint found for sequence $_currentSequenceNumber');
        return;
      }

      // Use YOLO detection and dynamic threshold with inpainting for navigation accuracy
      final embeddingStarted = _observationClock();
      final navigationResult = await _clipService
          .generateNavigationEmbeddingWithInpainting(
            imageFile,
            cleanSceneThreshold: _cleanSceneThreshold,
            peoplePresentThreshold: _peoplePresentThreshold,
            crowdedSceneThreshold: _crowdedSceneThreshold,
            disableYolo: _disableYolo,
          );
      final embeddingMs =
          _observationClock().difference(embeddingStarted).inMilliseconds;
      if (generation != _sessionGeneration ||
          _state != NavigationState.navigating ||
          sequence != _currentSequenceNumber)
        return;
      _lastCapturedEmbedding = navigationResult.embedding;

      // HALLWAY FIX: Use higher threshold for waypoints BEFORE turns to prevent premature turn instructions
      // Also use higher threshold for final waypoint to ensure accurate destination reaching
      final nextWaypoint = _getWaypointBySequence(_currentSequenceNumber + 1);
      final isBeforeTurnWaypoint =
          nextWaypoint != null &&
          (nextWaypoint.turnType == TurnType.left ||
              nextWaypoint.turnType == TurnType.right);
      final isFinalWaypoint =
          nextWaypoint == null; // No next waypoint means this is the final one

      _currentWaypointThreshold = routeWaypointThreshold(
        currentTurn: targetWaypoint.turnType,
        nextTurn: nextWaypoint?.turnType,
      );

      print(
        '🎯 Navigation frame: Sequence $_currentSequenceNumber (landmark: ${targetWaypoint.landmarkDescription ?? 'No description'})',
      );
      String thresholdReason =
          isFinalWaypoint
              ? 'FINAL WAYPOINT'
              : isBeforeTurnWaypoint
              ? 'BEFORE TURN'
              : targetWaypoint.turnType != TurnType.straight
              ? 'TURN WAYPOINT'
              : 'NORMAL';
      print(
        '🎯 Dynamic threshold: ${_currentWaypointThreshold.toStringAsFixed(2)} ($thresholdReason)',
      );
      print('🎯 YOLO Detection: ${_disableYolo ? 'DISABLED' : 'ENABLED'}');
      print(
        '👥 People detected: ${navigationResult.peopleDetected ? 'YES (${navigationResult.peopleCount})' : 'NO'}',
      );
      print(
        '🎨 Inpainting: ${_disableYolo
            ? 'DISABLED BY USER'
            : navigationResult.peopleDetected
            ? 'Applied (people + carried objects removed)'
            : 'Skipped (no people detected)'}',
      );

      // Calculate similarity with target waypoint
      final similarity = _calculateCosineSimilarity(
        navigationResult.embedding,
        targetWaypoint.embedding,
      );
      print(
        ' Similarity: ${similarity.toStringAsFixed(3)} (threshold: ${_currentWaypointThreshold.toStringAsFixed(2)})',
      );

      final scores = <int, double>{
        for (final point in route.waypoints)
          point.sequenceNumber: _calculateCosineSimilarity(
            navigationResult.embedding,
            point.embedding,
          ),
      };
      final criticalSequences = <int>{
        for (final point in route.waypoints)
          if (routeWaypointThreshold(
                currentTurn: point.turnType,
                nextTurn:
                    _getWaypointBySequence(point.sequenceNumber + 1)?.turnType,
              ) >=
              navigationCriticalWaypointThreshold)
            point.sequenceNumber,
      };
      final forwardLimit = navigationForwardLimit(
        targetSequence: sequence,
        criticalSequences: criticalSequences,
        lastSequence: _getTotalSequenceNumbers(),
      );
      final previousWaypoint = _getWaypointBySequence(sequence - 1);
      final turnedInPlace =
          previousWaypoint != null &&
          navigationTurnConfirmedInPlace(
            turn: targetWaypoint.turnType,
            incomingHeading: previousWaypoint.heading,
            targetHeading: targetWaypoint.heading,
            currentHeading: _currentHeading,
          );
      final decision = _waypointTracker.observe(
        targetSequence: sequence,
        scores: scores,
        threshold: _currentWaypointThreshold,
        capturedAt: started,
        forwardLimit: forwardLimit,
        movementCount: movement,
        turnInPlace: turnedInPlace,
      );
      final hasVisualMatch = decision.accepted;
      _waitingForPositionConfirmation =
          decision.reason == NavigationMatchReason.waitingMovement;
      final candidates = [
        for (final entry in scores.entries)
          {'sequence': entry.key, 'score': entry.value},
      ]..sort((a, b) => (b['score'] as double).compareTo(a['score'] as double));
      _matchingStatus =
          '参考范围 ${sequence + 1}～${forwardLimit + 1}：${decision.message}\n当前点 ${similarity.toStringAsFixed(3)} / 门槛 ${_currentWaypointThreshold.toStringAsFixed(2)}';
      _diagnostics.add({
        'type': 'frame',
        'target': sequence,
        'confirmed': sequence,
        'score': similarity,
        'threshold': _currentWaypointThreshold,
        'matched': hasVisualMatch,
        'decision': decision.reason.name,
        'decision_message': decision.message,
        'confirmations': decision.confirmations,
        'required_confirmations': decision.requiredConfirmations,
        'capture_ms': captureMs,
        'motion_ms': motionMs,
        'embedding_ms': embeddingMs,
        'request': _clipService.lastEmbeddingTiming,
        'forward_limit': forwardLimit,
        'candidate_sequence': decision.candidateSequence,
        'candidate_score': scores[decision.candidateSequence],
        'steps': _motion.steps,
        'motion_available': _motion.available,
        'manual_movement': _manualMovement,
        'turn_in_place': turnedInPlace,
        'scores': {
          for (final entry in scores.entries) '${entry.key}': entry.value,
        },
        'heading': _currentHeading,
        'reference_heading': targetWaypoint.heading,
        'elapsed_ms': _observationClock().difference(started).inMilliseconds,
        'candidates': candidates.take(3).toList(),
      });

      print('🔍 Waypoint validation for sequence $_currentSequenceNumber:');
      print(
        '   👁️ Visual match (≥${_currentWaypointThreshold.toStringAsFixed(2)}): $hasVisualMatch (${similarity.toStringAsFixed(3)})',
      );
      print(
        '   画面确认: ${decision.confirmations}/${decision.requiredConfirmations}；${decision.message}',
      );
      print(
        '   🎯 Target: ${targetWaypoint.landmarkDescription ?? 'No description'}, Turn: ${targetWaypoint.turnType}',
      );
      print('   📍 Target sequence: ${targetWaypoint.sequenceNumber}');

      // Log detailed waypoint matching information
      final validationInfo = '''
          🔍 路线画面比对（未匹配不代表程序报错）
          ━━━━━━━━━━━━━━━━━━━━
          📍 正在寻找：第 ${sequence + 1} / ${route.waypoints.length} 个点
          Turn: ${targetWaypoint.turnType.toString().split('.').last.toUpperCase()} ($thresholdReason)
          🎯 YOLO: ${_disableYolo ? 'DISABLED' : 'ENABLED'}
          When Recorded: ${targetWaypoint.peopleDetected ? "YES (${targetWaypoint.peopleCount} people)" : "NO people"}
          Now Detecting: ${navigationResult.peopleDetected ? "YES (${navigationResult.peopleCount} people)" : "NO people"} ${_disableYolo ? "(YOLO disabled)" : "(YOLO active)"}
          🎨 Inpainting: ${_disableYolo
          ? "DISABLED BY USER"
          : navigationResult.peopleDetected
          ? "Applied (people removed)"
          : "Not needed"}
          🎯 门槛: ${_currentWaypointThreshold.toStringAsFixed(3)} ($thresholdReason)
          👁️ 相似度: ${similarity.toStringAsFixed(3)}
          ${hasVisualMatch ? "✅" : "⏳"} ${decision.message}
          ${decision.requiredConfirmations == 1 ? '直行快速匹配' : '关键点连续确认'}：${decision.confirmations} / ${decision.requiredConfirmations}；参考范围 ${sequence + 1}～${forwardLimit + 1}
          本次特征请求：${(embeddingMs / 1000).toStringAsFixed(1)} 秒
          $motionHint
          站着等待不会自动完成路线。
          🔍 Embedding: ${_lastCapturedEmbedding?.length ?? 0} dims
          ━━━━━━━━━━━━━━━━━━━━
      ''';

      _updateDebugDisplay(validationInfo);

      // Straight views use one fresh match plus movement; critical views use two.
      if (hasVisualMatch) {
        print('✅ All conditions met - Waypoint reached!');
        // Reset failure counter on successful waypoint match
        _consecutiveWaypointFailures = 0;
        // Record visual match timestamp for guidance logic
        _lastVisualMatchTime = DateTime.now();

        // Provide guidance immediately when there's a visual match
        final acceptedWaypoint =
            _getWaypointBySequence(decision.candidateSequence!)!;
        // The final view completes navigation; do not announce an extra turn
        // before the arrival or wait for optional destination metadata.
        if (acceptedWaypoint.sequenceNumber < _getTotalSequenceNumbers()) {
          await _provideGuidance(
            acceptedWaypoint,
            scores[decision.candidateSequence]!,
            confirmed: true,
            turnCompleted: turnedInPlace,
          );
        }
        if (generation != _sessionGeneration ||
            _state != NavigationState.navigating ||
            sequence != _currentSequenceNumber)
          return;

        await _waypointReached(decision.candidateSequence!);
      } else if (decision.eligible ||
          decision.reason == NavigationMatchReason.repeatedFrame ||
          decision.reason == NavigationMatchReason.waitingMovement) {
        // A candidate awaiting its second confirmation is not a failed match.
        _consecutiveWaypointFailures = 0;
      } else {
        // Track consecutive waypoint progression failures
        print(
          'Waypoint not reached - similarity: ${similarity.toStringAsFixed(3)} (need ≥${_currentWaypointThreshold.toStringAsFixed(2)})',
        );
        await _handleWaypointFailure(targetWaypoint, similarity);
      }
    } catch (e) {
      if (generation != _sessionGeneration ||
          sequence != _currentSequenceNumber)
        return;
      _waypointTracker.interrupt();
      _matchingStatus = '导航图像处理失败，请检查服务器连接；本次未推进';
      _diagnostics.add({
        'type': 'error',
        'target': sequence,
        'message': _matchingStatus,
        'elapsed_ms': _observationClock().difference(started).inMilliseconds,
      });
      _updateDebugDisplay(_matchingStatus);
      onStatusUpdate?.call(_matchingStatus);
    }
  }

  /// Handle manual repositioning when user is lost
  Future<void> requestRepositioning() async {
    final generation = _sessionGeneration;
    // Can't reposition during initial orientation
    if (_state == NavigationState.initialOrientation) {
      await _speak('请先完成朝向校准。');
      return;
    }

    // Reset visual match time when repositioning
    _lastVisualMatchTime = null;

    _setState(NavigationState.reorientingUser);

    await _speak('请缓慢扫描周围环境，重新确认路线。');
    onStatusUpdate?.call('正在重新定位，请缓慢扫描四周');

    // Give user time to reorient
    Timer(_repositioningTimeout, () {
      if (generation == _sessionGeneration &&
          _state == NavigationState.reorientingUser) {
        _setState(NavigationState.navigating);
        onStatusUpdate?.call('继续导航');
      }
    });
  }

  // Private methods

  void _setState(NavigationState newState) {
    if (_state != newState) {
      _state = newState;
      _diagnostics.add({
        'type': 'state',
        'state': newState.name,
        'confirmed': _currentSequenceNumber,
      });
      onStateChanged?.call(_state);
    }
  }

  Future<void> _initializeTTS() async {
    try {
      await _tts.setLanguage('zh-CN').timeout(const Duration(seconds: 2));
      await _tts.setSpeechRate(0.5).timeout(const Duration(seconds: 2));
      await _tts.setVolume(0.9).timeout(const Duration(seconds: 2));
    } catch (e) {
      print('语音初始化失败，继续使用屏幕导航：$e');
    }
  }

  Future<void> _initializeCompass() async {
    try {
      // Cancel any existing compass subscription
      _compassSubscription?.cancel();

      if (FlutterCompass.events != null) {
        print('🧭 Initializing compass...');
        _compassSubscription = FlutterCompass.events!.listen(
          (event) {
            if (event.heading != null) {
              final normalizedHeading = _normalizeHeading(event.heading!);
              _currentHeading = normalizedHeading;
              print(
                '🧭 Raw heading: ${event.heading!.toStringAsFixed(1)}°, Normalized: ${normalizedHeading.toStringAsFixed(1)}°',
              );
            } else {
              print('🧭 Compass reading is null');
            }
          },
          onError: (error) {
            print('🧭 Compass error: $error');
            onError?.call('Compass error: $error');
          },
        );
      } else {
        print('🧭 FlutterCompass.events is null - compass not available');
        onError?.call('Compass not available on this device');
      }
    } catch (e) {
      print('🧭 Failed to initialize compass: $e');
      onError?.call('Failed to initialize compass: $e');
    }
  }

  Future<void> _checkProgress() async {
    if (_currentRoute == null || _state != NavigationState.navigating) return;

    // Get current waypoint by sequence number (not array index!)
    final targetWaypoint = _getWaypointBySequence(_currentSequenceNumber);
    if (targetWaypoint == null) {
      print('No waypoint found for sequence $_currentSequenceNumber');
      return;
    }

    print(
      'Current target: Sequence $_currentSequenceNumber (landmark: ${targetWaypoint.landmarkDescription ?? 'No description'})',
    );

    // Guidance is now only provided when there's an actual visual match
    // This method is used for monitoring and recovery triggering only
    final now = DateTime.now();
    final timeSinceLastVisualMatch =
        _lastVisualMatchTime != null
            ? now.difference(_lastVisualMatchTime!)
            : Duration(days: 1); // If no match ever, set to a large duration

    print(
      '📊 Monitoring: Last visual match ${timeSinceLastVisualMatch.inSeconds}s ago',
    );

    // Could add recovery logic here if no visual match for too long
    // For now, just monitor without providing guidance
  }

  /// Find waypoint by sequence number (proper navigation logic)
  PathWaypoint? _getWaypointBySequence(int sequenceNumber) {
    if (_currentRoute?.waypoints == null) return null;

    try {
      return _currentRoute!.waypoints.firstWhere(
        (waypoint) => waypoint.sequenceNumber == sequenceNumber,
      );
    } catch (e) {
      return null; // No waypoint found with this sequence number
    }
  }

  /// Get total number of waypoints in route (max sequence number)
  int _getTotalSequenceNumbers() {
    if (_currentRoute?.waypoints == null || _currentRoute!.waypoints.isEmpty)
      return 0;

    return _currentRoute!.waypoints
        .map((waypoint) => waypoint.sequenceNumber)
        .reduce((a, b) => a > b ? a : b);
  }

  Future<void> _waypointReached(int acceptedSequence) async {
    final previousSequence = _currentSequenceNumber;
    _currentSequenceNumber = acceptedSequence;
    _diagnostics.add({
      'type': 'progress',
      'from': previousSequence,
      'accepted': acceptedSequence,
      'merged_straight_references': acceptedSequence - previousSequence,
    });
    _setState(NavigationState.approachingWaypoint);

    //Get current waypoint by sequence number
    final currentWaypoint = _getWaypointBySequence(_currentSequenceNumber);
    if (currentWaypoint == null) {
      print('Error: No waypoint found for sequence $_currentSequenceNumber');
      return;
    }

    //Confirm waypoint reached (silent for VIP users)
    print('✅ Waypoint ${_currentSequenceNumber} reached - moving to next');

    // NO AUDIO: Removed "waypoint reached" audio to reduce noise for VIP users
    // await _speak(message);
    // onStatusUpdate?.call(message);

    // CLEAR LAST SPOKEN INSTRUCTION so next waypoint instruction will always play
    _lastSpokenInstruction = null;
    _lastInstructionTime = null;

    print('🔄 Waypoint progression (visual-only mode)');

    // Move to NEXT SEQUENCE NUMBER, not array index!
    _currentSequenceNumber++;
    _matchingStatus = '已确认 $_currentSequenceNumber 个参考点，继续识别邻近画面';
    print('Moving to next sequence: $_currentSequenceNumber');

    // Check if there's a next waypoint
    final nextWaypoint = _getWaypointBySequence(_currentSequenceNumber);
    if (nextWaypoint == null) {
      // Destination reached - no more waypoints
      await _destinationReached();
    } else {
      // Continue to next waypoint
      _setState(NavigationState.navigating);
      // Keep a just-confirmed turn visible until the next reference is matched.
      if (currentWaypoint.turnType == TurnType.straight) {
        await _updateNavigationInstruction();
      }
    }
  }

  Future<void> _destinationReached() async {
    final generation = _sessionGeneration;
    final route = _currentRoute!;
    final message = '已到达${route.endNodeName}，本次导航完成。请停下。';
    _matchingStatus = message;
    _waitingForPositionConfirmation = false;
    _navigationTimer?.cancel();
    _orientationTransitionTimer?.cancel();
    unawaited(_compassSubscription?.cancel() ?? Future<void>.value());
    unawaited(_motion.stop());
    _diagnostics.add({
      'type': 'arrival',
      'destination': route.endNodeName,
      'confirmed': _currentSequenceNumber,
      'total': route.waypoints.length,
    });
    // Keep the completed route/count visible until the user dismisses it.
    _setState(NavigationState.destinationReached);
    if (generation != _sessionGeneration) return;
    onInstructionUpdate?.call(
      NavigationInstruction(
        type: InstructionType.arrive,
        displayText: '已到达${route.endNodeName}',
        spokenInstruction: message,
        targetHeading: route.waypoints.last.heading,
        confidence: 1,
        waypointNumber: route.waypoints.length,
        totalWaypoints: route.waypoints.length,
      ),
    );
    _updateDebugDisplay(
      '✅ $message\n已确认 ${route.waypoints.length} / ${route.waypoints.length} 个参考点 · 100%',
    );
    onStatusUpdate?.call(message);
    // Stop an older queued instruction, but a broken TTS plugin must not hold
    // the completion screen or the frame-processing future open indefinitely.
    if (onGuidance == null) {
      try {
        await _tts.stop().timeout(const Duration(milliseconds: 500));
      } catch (_) {}
    }
    if (generation != _sessionGeneration ||
        _state != NavigationState.destinationReached)
      return;
    await _speak(message);
  }

  /// Handle consecutive waypoint progression failures and trigger recovery system
  Future<void> _handleWaypointFailure(
    PathWaypoint targetWaypoint,
    double similarity,
  ) async {
    _consecutiveWaypointFailures++;
    if (_consecutiveWaypointFailures == 5) {
      await _speakSmart('暂未识别到路线，请停下核对位置和拍摄方向。');
    }
    print(
      '📊 Waypoint failure count: $_consecutiveWaypointFailures/$_waypointFailureThreshold',
    );

    // Only trigger recovery after 5 consecutive failures
    if (_consecutiveWaypointFailures >= _waypointFailureThreshold) {
      print('🚨 5 consecutive waypoint failures - starting recovery system');
      await _startWaypointRecovery();
    } else {
      // Don't provide guidance on waypoint failure
      // Only provide guidance when there's an actual visual match
      print(
        '⚠️ Waypoint failure ${_consecutiveWaypointFailures}/$_waypointFailureThreshold - waiting for visual match',
      );
    }
  }

  /// Start the waypoint recovery system
  Future<void> _startWaypointRecovery() async {
    final generation = _sessionGeneration;
    _setState(NavigationState.awaitingManualCapture);
    _manualCaptureCount = 0;
    _capturedRecoveryFrames.clear();
    // Reset visual match time when entering recovery
    _lastVisualMatchTime = null;

    await _speak('连续多次未匹配到路线，请停下并面向录制时的方向，拍摄画面重新确认位置。');
    if (generation != _sessionGeneration) return;
    onStatusUpdate?.call('正在恢复定位，准备手动拍摄');

    // Request UI to start/restart manual capture process (resets UI state)
    onRequestManualCapture?.call();

    // Add small delay to ensure UI state is reset before proceeding
    await Future.delayed(Duration(milliseconds: 100));
    if (generation != _sessionGeneration) return;

    // Start manual capture process
    await _requestNextManualCapture();
  }

  /// Request next manual capture from user
  Future<void> _requestNextManualCapture() async {
    if (_manualCaptureCount >= 3) {
      // All frames captured, analyze them
      await _analyzeRecoveryFrames();
      return;
    }

    _setState(NavigationState.manualCaptureInProgress);
    _manualCaptureCount++;

    await _speak('请面向前方，拍摄第 ${_manualCaptureCount}/3 张恢复画面。');
    onStatusUpdate?.call('请拍摄第 ${_manualCaptureCount}/3 张恢复画面');
  }

  /// Handle manually captured frame
  Future<void> processManualCapturedFrame(File imageFile) async {
    if (_state != NavigationState.manualCaptureInProgress) {
      print('⚠️ Unexpected manual capture - ignoring');
      return;
    }

    try {
      _capturedRecoveryFrames.add(imageFile);
      print('📸 Captured recovery frame ${_manualCaptureCount} of 3');

      onManualFrameCaptured?.call(imageFile);

      // Request next capture or start analysis
      await _requestNextManualCapture();
    } catch (e) {
      onError?.call('Error processing manual capture: $e');
    }
  }

  /// Analyze captured recovery frames and rediscover waypoint
  Future<void> _analyzeRecoveryFrames() async {
    final generation = _sessionGeneration;
    _setState(NavigationState.analyzingCapturedFrames);

    await _speak('正在分析画面。');
    if (generation != _sessionGeneration) return;
    onStatusUpdate?.call('正在分析拍摄画面…');

    try {
      // Generate embeddings for all captured frames
      List<List<double>> frameEmbeddings = [];
      final frames = List<File>.of(_capturedRecoveryFrames);
      for (int i = 0; i < frames.length; i++) {
        final file = frames[i];
        print(
          '🔍 Processing recovery frame ${i + 1}/${_capturedRecoveryFrames.length}...',
        );

        final navigationResult = await _clipService
            .generateNavigationEmbeddingWithInpainting(
              file,
              cleanSceneThreshold: _cleanSceneThreshold,
              peoplePresentThreshold: _peoplePresentThreshold,
              crowdedSceneThreshold: _crowdedSceneThreshold,
              disableYolo: _disableYolo,
            );
        if (generation != _sessionGeneration) return;

        frameEmbeddings.add(navigationResult.embedding);
        print(
          '✅ Generated embedding for frame ${i + 1} (${navigationResult.embedding.length} dims)',
        );
      }

      // Compare against all waypoints in current route using majority voting
      final bestMatch = await _findBestWaypointMatch(frameEmbeddings);
      if (generation != _sessionGeneration) return;

      if (bestMatch != null && bestMatch['similarity'] >= _recoveryThreshold) {
        final matchedWaypoint = bestMatch['waypoint'] as PathWaypoint;
        final similarity = bestMatch['similarity'] as double;
        final votes = bestMatch['votes'] as int;

        print(
          '✅ Recovery successful: Found waypoint ${matchedWaypoint.sequenceNumber}',
        );
        print(
          '   Similarity: ${similarity.toStringAsFixed(3)} (≥${_recoveryThreshold})',
        );
        print('   Votes: $votes/${frameEmbeddings.length}');

        final wouldSkip =
            matchedWaypoint.sequenceNumber > _currentSequenceNumber;
        final resumeMessage =
            wouldSkip
                ? '恢复画面更像第 ${matchedWaypoint.sequenceNumber + 1} 个点，保持寻找第 ${_currentSequenceNumber + 1} 个点，不跳过未确认采样点'
                : '恢复画面最接近第 ${matchedWaypoint.sequenceNumber + 1} 个点，继续核对路线';
        await _speak(
          wouldSkip ? '已取得恢复画面，请核对路线，不跳过尚未确认的采样点。' : '已找到参考位置，继续确认路线。',
        );
        if (generation != _sessionGeneration) return;
        onStatusUpdate?.call(resumeMessage);

        // Recovery may rewind a mistaken position, but forward progress must
        // still pass the same sequential relative and temporal confirmation.
        if (!wouldSkip) _currentSequenceNumber = matchedWaypoint.sequenceNumber;
        _waypointTracker.reset(movementCount: _motion.steps + _manualMovement);
        _matchingStatus = resumeMessage;
        _diagnostics.add({
          'type': 'recovery',
          'target': _currentSequenceNumber,
          'candidate_sequence': matchedWaypoint.sequenceNumber,
          'forward_jump_blocked': wouldSkip,
          'message': resumeMessage,
          'score': similarity,
          'votes': votes,
        });
        _consecutiveWaypointFailures = 0;
        _recoveryAttempts = 0;
        // Set visual match time to now since we found the location
        _lastVisualMatchTime = DateTime.now();

        _setState(NavigationState.navigating);
        // Provide guidance after successful recovery
        await _updateNavigationInstruction();

        // Clean up captured frames
        await _cleanupRecoveryFrames();
      } else {
        // Recovery failed - try moving backward
        await _handleRecoveryFailure();
      }
    } catch (e) {
      if (generation != _sessionGeneration) return;
      onError?.call('Error analyzing recovery frames: $e');
      await _handleRecoveryFailure();
    }
  }

  /// Find best waypoint match using majority voting
  Future<Map<String, dynamic>?> _findBestWaypointMatch(
    List<List<double>> frameEmbeddings,
  ) async {
    if (_currentRoute?.waypoints == null || frameEmbeddings.isEmpty)
      return null;

    // Track votes for each waypoint
    final waypointVotes = <int, int>{}; // sequenceNumber -> vote count
    final waypointSimilarities =
        <int, List<double>>{}; // sequenceNumber -> similarities

    print(
      '🔍 Comparing ${frameEmbeddings.length} frames against ${_currentRoute!.waypoints.length} waypoints',
    );

    // Compare each frame against all waypoints
    for (
      int frameIndex = 0;
      frameIndex < frameEmbeddings.length;
      frameIndex++
    ) {
      final frameEmbedding = frameEmbeddings[frameIndex];

      double bestSimilarityForFrame = 0.0;
      int? bestWaypointForFrame;

      for (final waypoint in _currentRoute!.waypoints) {
        final similarity = _calculateCosineSimilarity(
          frameEmbedding,
          waypoint.embedding,
        );

        // Initialize tracking for this waypoint
        waypointSimilarities.putIfAbsent(waypoint.sequenceNumber, () => []);
        waypointSimilarities[waypoint.sequenceNumber]!.add(similarity);

        // Check if this is the best match for this frame
        if (similarity > bestSimilarityForFrame &&
            similarity >= _recoveryThreshold) {
          bestSimilarityForFrame = similarity;
          bestWaypointForFrame = waypoint.sequenceNumber;
        }
      }

      // Vote for the best waypoint for this frame
      if (bestWaypointForFrame != null) {
        waypointVotes[bestWaypointForFrame] =
            (waypointVotes[bestWaypointForFrame] ?? 0) + 1;
        print(
          '  Frame ${frameIndex + 1}: Votes for waypoint $bestWaypointForFrame (similarity: ${bestSimilarityForFrame.toStringAsFixed(3)})',
        );
      } else {
        print('  Frame ${frameIndex + 1}: No waypoint above threshold');
      }
    }

    // Find waypoint with most votes
    if (waypointVotes.isEmpty) return null;

    int bestSequenceNumber = 0;
    int maxVotes = 0;

    for (final entry in waypointVotes.entries) {
      if (entry.value > maxVotes) {
        maxVotes = entry.value;
        bestSequenceNumber = entry.key;
      }
    }

    // Check if we have majority (more than half)
    final majorityThreshold = (frameEmbeddings.length / 2).ceil();
    if (maxVotes < majorityThreshold) {
      print(
        '❌ No majority: Best waypoint $bestSequenceNumber has $maxVotes votes (need ≥$majorityThreshold)',
      );
      return null;
    }

    // Calculate average similarity for the winning waypoint
    final similarities = waypointSimilarities[bestSequenceNumber] ?? [];
    final averageSimilarity =
        similarities.isEmpty
            ? 0.0
            : similarities.reduce((a, b) => a + b) / similarities.length;

    // Find the waypoint object
    final waypoint = _currentRoute!.waypoints.firstWhere(
      (w) => w.sequenceNumber == bestSequenceNumber,
    );

    return {
      'waypoint': waypoint,
      'similarity': averageSimilarity,
      'votes': maxVotes,
    };
  }

  /// Handle recovery failure and request backward movement
  Future<void> _handleRecoveryFailure() async {
    final generation = _sessionGeneration;
    _recoveryAttempts++;

    if (_recoveryAttempts >= _maxRecoveryAttempts) {
      // Max attempts reached - give up
      _setState(NavigationState.recoveryFailed);

      await _speak('无法确定位置，请手动调整位置或重新开始。');
      onStatusUpdate?.call('恢复定位失败，请调整位置后重试');

      await _cleanupRecoveryFrames();
      return;
    }

    // Request user to move backward and try again
    await _speak('未找到位置，请返回刚才的位置后重试。');
    onStatusUpdate?.call('恢复定位失败，请返回刚才的位置后重试');

    // Wait for user to move backward, then retry
    Timer(Duration(seconds: 5), () async {
      if (generation == _sessionGeneration &&
          _state == NavigationState.analyzingCapturedFrames) {
        await _speak('请停下并面向前方，重新尝试。');
        await Future.delayed(Duration(seconds: 2));
        if (generation != _sessionGeneration) return;
        // Clean up any lingering recovery data before restarting
        await _cleanupRecoveryFrames();
        await _startWaypointRecovery();
      }
    });
  }

  /// Clean up captured recovery frames
  Future<void> _cleanupRecoveryFrames() async {
    try {
      for (final file in _capturedRecoveryFrames) {
        if (await file.exists()) {
          await file.delete();
        }
      }
      _capturedRecoveryFrames.clear();
      _manualCaptureCount = 0;

      print('🧹 Cleaned up ${_capturedRecoveryFrames.length} recovery frames');
    } catch (e) {
      print('⚠️ Error cleaning up recovery frames: $e');
    }
  }

  Future<void> _provideGuidance(
    PathWaypoint targetWaypoint,
    double similarity, {
    bool confirmed = false,
    bool turnCompleted = false,
  }) async {
    final generation = _sessionGeneration;
    final now = DateTime.now();
    final timeSinceLastGuidance = now.difference(_lastGuidanceTime);

    // Don't provide guidance too frequently - set to 5 seconds for better audio spacing
    if (timeSinceLastGuidance < Duration(seconds: 5) &&
        !(confirmed && targetWaypoint.turnType != TurnType.straight))
      return;

    _lastGuidanceTime = now;

    // Create navigation instruction
    final instruction = await _createNavigationInstruction(
      targetWaypoint,
      similarity,
      turnCompleted: turnCompleted,
    );
    if (generation != _sessionGeneration) return;
    onInstructionUpdate?.call(instruction);

    // Speak the instruction using smart speaking to prevent repetition
    await _speakSmart(instruction.spokenInstruction);

    // Update status
    onStatusUpdate?.call(instruction.displayText);
  }

  Future<NavigationInstruction> _createNavigationInstruction(
    PathWaypoint targetWaypoint,
    double similarity, {
    bool turnCompleted = false,
  }) async {
    // 🚨 FIX: Display waypoint number = sequence + 1 (user-friendly)
    final waypointNumber = _currentSequenceNumber + 1; // Show 1,2,3... to user
    final totalWaypoints =
        _getTotalSequenceNumbers() + 1; // Total count for display

    String displayText;
    String spokenInstruction;
    InstructionType instructionType;

    // 🎯 SIMPLIFIED: Only 3 commands - continue straight, turn left, turn right
    switch (targetWaypoint.turnType) {
      case TurnType.straight:
        instructionType = InstructionType.continue_;
        displayText = '继续直行';
        spokenInstruction = '继续直行';
        break;
      case TurnType.left:
        instructionType = InstructionType.turnLeft;
        displayText = '左转';
        spokenInstruction = '左转';
        break;
      case TurnType.right:
        instructionType = InstructionType.turnRight;
        displayText = '右转';
        spokenInstruction = '右转';
        break;
    }

    if (turnCompleted && targetWaypoint.turnType != TurnType.straight) {
      final direction = targetWaypoint.turnType == TurnType.left ? '左' : '右';
      instructionType = InstructionType.continue_;
      displayText = '${direction}转已确认，沿当前方向继续';
      spokenInstruction = displayText;
    }

    return NavigationInstruction(
      type: instructionType,
      displayText: displayText,
      spokenInstruction: spokenInstruction,
      targetHeading: targetWaypoint.heading,
      confidence: similarity,
      distanceToTarget: targetWaypoint.distanceFromPrevious,
      waypointNumber: waypointNumber,
      totalWaypoints: totalWaypoints,
    );
  }

  Future<void> _updateNavigationInstruction() async {
    final generation = _sessionGeneration;
    if (_currentRoute == null) return;

    // Get waypoint by sequence number, not array index
    final targetWaypoint = _getWaypointBySequence(_currentSequenceNumber);
    if (targetWaypoint == null) {
      print('No waypoint found for sequence $_currentSequenceNumber');
      return;
    }

    // A turn instruction is issued only after its visual reference is confirmed.
    final instruction =
        targetWaypoint.turnType == TurnType.straight
            ? await _createNavigationInstruction(targetWaypoint, 0.5)
            : NavigationInstruction(
              type: InstructionType.continue_,
              displayText:
                  '接近转弯参考点（${targetWaypoint.turnType == TurnType.left ? '左转' : '右转'}），请在拐角核对位置',
              spokenInstruction:
                  '接近${targetWaypoint.turnType == TurnType.left ? '左' : '右'}转弯处，请在拐角核对位置和朝向。',
              targetHeading: targetWaypoint.heading,
              confidence: 0,
              waypointNumber: _currentSequenceNumber + 1,
              totalWaypoints: _getTotalSequenceNumbers() + 1,
            );
    if (generation != _sessionGeneration) return;

    onInstructionUpdate?.call(instruction);
    await _speakSmart(instruction.spokenInstruction);
  }

  Future<void> _speak(String text) async {
    if (onGuidance != null) {
      onGuidance!(text);
      return;
    }
    try {
      speakWithoutBlocking(() => _tts.speak(text));
    } catch (e) {
      print('Error speaking: $e');
    }
  }

  /// Smart speak method that prevents repeated instructions for VIP convenience
  Future<void> _speakSmart(String text) async {
    final now = DateTime.now();

    // Check if this is the same instruction as last time
    if (_lastSpokenInstruction == text && _lastInstructionTime != null) {
      final timeSinceLastInstruction = now.difference(_lastInstructionTime!);

      // Only repeat the instruction if enough time has passed (15 seconds instead of 5)
      // This prevents confusion when the app gives "turn right" multiple times
      if (timeSinceLastInstruction < Duration(seconds: 15)) {
        print(
          '🔇 Skipping repeated instruction: "$text" (last spoken ${timeSinceLastInstruction.inSeconds}s ago)',
        );
        return;
      }
    }

    // Speak the instruction and update tracking
    print('Speaking: "$text"');
    _lastSpokenInstruction = text;
    _lastInstructionTime = now;
    await _speak(text);
  }

  double _calculateCosineSimilarity(List<double> vec1, List<double> vec2) {
    if (vec1.length != vec2.length) return 0.0;

    double dotProduct = 0.0;
    double normVec1 = 0.0;
    double normVec2 = 0.0;

    for (int i = 0; i < vec1.length; i++) {
      dotProduct += vec1[i] * vec2[i];
      normVec1 += vec1[i] * vec1[i];
      normVec2 += vec2[i] * vec2[i];
    }

    normVec1 = sqrt(normVec1);
    normVec2 = sqrt(normVec2);

    if (normVec1 == 0 || normVec2 == 0) return 0.0;

    return dotProduct / (normVec1 * normVec2);
  }

  // Calculate dynamic threshold based on waypoint people_detected field vs current frame
  // HALLWAY FIX: Higher thresholds for waypoints BEFORE turns to prevent premature turn instructions

  void dispose() {
    stopNavigation();
    if (onGuidance == null) _tts.stop();
  }
}

/// Represents a navigation instruction
class NavigationInstruction {
  final InstructionType type;
  final String displayText;
  final String spokenInstruction;
  final double targetHeading;
  final double confidence;
  final double? distanceToTarget;
  final int waypointNumber;
  final int totalWaypoints;

  NavigationInstruction({
    required this.type,
    required this.displayText,
    required this.spokenInstruction,
    required this.targetHeading,
    required this.confidence,
    this.distanceToTarget,
    required this.waypointNumber,
    required this.totalWaypoints,
  });
}
