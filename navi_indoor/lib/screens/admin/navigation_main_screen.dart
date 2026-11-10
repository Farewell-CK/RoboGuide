import '../../widgets/navigation_progress_panel.dart';
import '../../widgets/navigation_arrival_panel.dart';
import 'dart:async';
import '../../models/orientation_math.dart';
import '../localization_diagnostics_screen.dart';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_tts/flutter_tts.dart';
import '../../services/dinov2_service.dart';
import '../../services/supabase_service.dart';
import '../../services/position_localization_service.dart';
import '../../services/real_time_navigation_service.dart' as nav_service;
import '../../services/voice_assistant_service.dart';
import '../../widgets/compass_painter.dart';
import '../../widgets/navigation_content_frame.dart';
import '../../widgets/localization_ready_panel.dart';
import '../../services/navigation_camera_session.dart';
import '../../services/navigation_prompts.dart';
import '../../widgets/navigation_prompt_panel.dart';
import '../../widgets/debug_overlay.dart';

class NavigationMainScreen extends StatefulWidget {
  final CameraDescription camera;

  const NavigationMainScreen({Key? key, required this.camera})
    : super(key: key);

  @override
  State<NavigationMainScreen> createState() => _NavigationMainScreenState();
}

class _NavigationMainScreenState extends State<NavigationMainScreen>
    with WidgetsBindingObserver {
  // Services
  late ClipService _clipService;
  late SupabaseService _supabaseService;
  late PositionLocalizationService _localizationService;
  late nav_service.RealTimeNavigationService _navigationService;
  late VoiceAssistantService _voiceAssistant;

  // TTS for location announcements
  late FlutterTts _flutterTts;
  late NavigationPrompts _prompts;

  // Camera
  late final _cameraSession = NavigationCameraSession(_createCameraController);
  CameraController? get _cameraController => _cameraSession.controller;
  bool _isCameraInitialized = false;

  // State
  NavigationScreenState _screenState = NavigationScreenState.initializing;
  LocationMatch? _currentLocation;
  List<NavigationRoute> _availableRoutes = [];
  NavigationRoute? _selectedRoute;
  String _statusMessage = '正在初始化…';
  nav_service.NavigationInstruction? _currentInstruction;

  // UI State
  bool _isProcessingFrame = false;
  bool _localizationBusy = false;
  bool _cancelLocalization = false;
  bool _isTakingPicture = false; // New: Only for camera access
  Timer? _frameProcessingTimer;

  // Enhanced localization state
  String? _localizationResult;
  bool _showLocalizationResult = false;
  String? _gptApiKey;
  bool _useVLMVerification = true;

  // YOLO Detection toggle
  bool _disableYolo = true; // Default to disabled for better performance

  // Debug overlay (admin feature)
  bool _isDebugVisible = false;
  String _debugInfo = '''
🐛 DEBUG MODE - NAVIGATION SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Current State: Ready to localize
🎯 Navigation Service: Initialized
⏳ Waiting for user to start localization...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
''';

  // Voice assistant state
  VoiceSessionState _voiceState = VoiceSessionState.idle;
  String _voiceTranscript = '';
  String _voiceResponse = '';
  bool _isVoiceEnabled = true;
  bool _isVoiceInitialized = false;

  // Recovery system state
  bool _isInRecoveryMode = false;
  int _recoveryFrameCount = 0;
  bool _isCapturingRecoveryFrame = false;

  // Compass orientation state
  double? _currentHeading;
  double? _targetHeading;
  bool _isInOrientationMode = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _initializeServices();
    _loadApiKey();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _cancelLocalization = true;
    _frameProcessingTimer?.cancel();
    unawaited(_cameraSession.close());
    _navigationService.dispose();
    _localizationService.dispose();
    _voiceAssistant.dispose();
    _prompts.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      // Stop scans before detaching the controller. Returning from settings or
      // Tailscale must never resume a scan holding the old camera instance.
      _cancelLocalization = true;
      _prompts.suspend();
      _disposeCamera();
    } else if (state == AppLifecycleState.resumed) {
      _prompts.resume();
      // Reinitialize camera when app resumes
      if (!_isCameraInitialized) {
        _initializeCamera();
      }
    }
  }

  Future<void> _initializeServices() async {
    try {
      // Initialize services
      _supabaseService = SupabaseService();
      _clipService = ClipService();
      _localizationService = PositionLocalizationService(
        clipService: _clipService,
        supabaseService: _supabaseService,
      );
      _voiceAssistant = VoiceAssistantService();
      _flutterTts = FlutterTts();
      _prompts = NavigationPrompts(
        speak: _flutterTts.speak,
        stop: _flutterTts.stop,
        configure: _configureSpeech,
      );
      _flutterTts.setErrorHandler((_) => _prompts.reportVoiceError());
      _navigationService = nav_service.RealTimeNavigationService(
        clipService: _clipService,
        onStateChanged: _onNavigationStateChanged,
        onStatusUpdate: _onStatusUpdate,
        onInstructionUpdate: _onInstructionUpdate,
        onError: _onError,
        onGuidance: _onNavigationGuidance,
        onDebugUpdate: _onDebugUpdate,
        onRequestManualCapture: _onRequestManualCapture,
        onManualFrameCaptured: _onManualFrameCaptured,
      );

      // Initialize camera
      await _initializeCamera();

      if (!mounted || !_isCameraInitialized) return;
      setState(() {
        _screenState = NavigationScreenState.readyToLocalize;
        _statusMessage = '请站在已录制的节点附近，点击“开始定位”';
      });
      _prompts.show(
        '准备就绪，请站在已录制的节点附近，点击开始定位。',
        kind: NavigationPromptKind.progress,
      );
      if ((dotenv.env['OPENAI_API_KEY'] ?? '').isNotEmpty &&
          (dotenv.env['PICOVOICE_ACCESS_KEY'] ?? '').isNotEmpty) {
        unawaited(_initializeVoiceAssistant());
      }
    } catch (e) {
      _onError('初始化失败： $e');
    }
  }

  Future<void> _configureSpeech() async {
    final available = await _flutterTts.isLanguageAvailable('zh-CN');
    if (available == false || available == 0) throw StateError('手机未安装中文语音');
    final result = await _flutterTts.setLanguage('zh-CN');
    if (result == 0) throw StateError('无法启用中文语音');
    await _flutterTts.awaitSpeakCompletion(false);
    await _flutterTts.setQueueMode(0);
    await _flutterTts.setSpeechRate(0.45);
    await _flutterTts.setVolume(1.0);
  }

  Future<void> _loadApiKey() async {
    try {
      _gptApiKey = dotenv.env['OPENAI_API_KEY'];
      if (_gptApiKey == null || _gptApiKey!.isEmpty) {
        print('⚠️ OpenAI API key not found in .env file');
        _useVLMVerification = false;
      } else {
        print('✅ OpenAI API key loaded successfully');
        _useVLMVerification = true;
      }
    } catch (e) {
      print('⚠️ Error loading API key: $e');
      _useVLMVerification = false;
    }
  }

  /// Initialize voice assistant with callbacks
  Future<void> _initializeVoiceAssistant() async {
    try {
      // Set up callbacks
      _voiceAssistant.onStateChanged = (state) {
        setState(() {
          _voiceState = state;
        });
      };

      _voiceAssistant.onStatusUpdate = (status) {
        print('🎤 Voice: $status');
      };

      _voiceAssistant.onTranscriptUpdate = (transcript) {
        setState(() {
          _voiceTranscript = transcript;
        });
      };

      _voiceAssistant.onResponse = (response) {
        setState(() {
          _voiceResponse = response;
        });
      };

      _voiceAssistant.onCommandDetected = (command) {
        _handleVoiceCommand(command);
      };

      _voiceAssistant.onError = (error) {
        _onError('Voice Assistant: $error');
      };

      // Initialize the voice assistant
      final success = await _voiceAssistant.initialize(
        clipService: _clipService,
        supabaseService: _supabaseService,
        navigationService: _navigationService,
        localizationService: _localizationService,
      );

      setState(() {
        _isVoiceInitialized = success;
      });

      if (success) {
        print('✅ Voice Assistant initialized and listening for "Hey Navi"');
      } else {
        print('⚠️ Voice Assistant initialization failed');
      }
    } catch (e) {
      print('❌ Voice Assistant initialization error: $e');
      setState(() {
        _isVoiceInitialized = false;
      });
    }
  }

  /// Handle voice commands
  void _handleVoiceCommand(VoiceCommand command) {
    print('🎤 Voice Command: ${command.intent} - ${command.originalText}');

    switch (command.intent) {
      case VoiceIntent.localize:
        _handleVoiceLocalize();
        break;
      case VoiceIntent.navigate:
        _handleVoiceNavigate(command.parameters['destination'] ?? '');
        break;
      case VoiceIntent.speakLocation:
        _handleVoiceSpeakLocation();
        break;
      case VoiceIntent.stop:
        _handleVoiceStop();
        break;
      default:
        // Voice assistant handles unknown commands
        break;
    }
  }

  /// Handle voice localization command
  void _handleVoiceLocalize() {
    if (!_isCameraInitialized || _cameraController == null) {
      _voiceAssistant.speak('Camera is not ready for relocalization.');
      return;
    }

    // Allow relocalization from any state (not just readyToLocalize)
    if (_screenState == NavigationScreenState.processingLocation) {
      _voiceAssistant.speak('Localization is already in progress.');
      return;
    }

    // Stop any current navigation if active
    if (_screenState == NavigationScreenState.navigating) {
      _navigationService.stopNavigation();
    }

    // Start enhanced localization regardless of current state
    _startEnhancedLocalization();
  }

  /// Handle voice navigation command
  void _handleVoiceNavigate(String destination) {
    if (_currentLocation == null) {
      _voiceAssistant.speak(
        'Please find your location first by saying "where am I".',
      );
      return;
    }

    if (_availableRoutes.isEmpty) {
      _voiceAssistant.speak('No routes available from your current location.');
      return;
    }

    // TODO: Update route matching when NavigationRoute structure is finalized
    // Try to find matching route (placeholder implementation)
    /*
    final matchingRoute = _availableRoutes.where((route) => 
      route.destinationName.toLowerCase().contains(destination.toLowerCase())
    ).firstOrNull;
    
    if (matchingRoute != null) {
      setState(() {
        _selectedRoute = matchingRoute;
      });
      _startNavigation();
      _voiceAssistant.speak('Starting navigation to ${matchingRoute.destinationName}.');
    } else {
      _voiceAssistant.speak('I couldn\'t find a route to $destination. Available destinations include: ${_availableRoutes.map((r) => r.destinationName).join(', ')}.');
    }
    */

    // Placeholder response for voice navigation
    _voiceAssistant.speak(
      'Voice navigation to $destination will be available once route structure is finalized.',
    );
  }

  /// Handle voice speak location command
  void _handleVoiceSpeakLocation() {
    if (_currentLocation != null) {
      String locationInfo =
          'You are currently at ${_currentLocation!.nodeName}';
      if (_localizationResult != null) {
        // Extract confidence from localization result
        final confidenceMatch = RegExp(
          r'(\d+)% confidence',
        ).firstMatch(_localizationResult!);
        if (confidenceMatch != null) {
          locationInfo +=
              ' with ${confidenceMatch.group(1)} percent confidence';
        }
      }
      _voiceAssistant.speak(locationInfo);
    } else {
      _voiceAssistant.speak(
        'Your location is not currently determined. Say "where am I" to find your location.',
      );
    }
  }

  /// Handle voice stop command
  void _handleVoiceStop() {
    if (_screenState == NavigationScreenState.navigating) {
      _stopAndReturnToMain();
      _voiceAssistant.speak('导航已停止。');
    } else if (_screenState == NavigationScreenState.processingLocation) {
      // Can't easily stop localization mid-process, but acknowledge
      _voiceAssistant.speak(
        'Location detection is in progress and will complete shortly.',
      );
    } else {
      _voiceAssistant.speak('Nothing to stop.');
    }
  }

  Future<CameraController> _createCameraController() async {
    final permissions =
        await [
          Permission.camera,
          Permission.microphone,
          Permission.activityRecognition,
          Permission.sensors,
        ].request();
    if (permissions[Permission.camera] != PermissionStatus.granted) {
      throw Exception('相机权限未开启，请在系统设置中允许使用相机');
    }
    return CameraController(
      widget.camera,
      ResolutionPreset.medium,
      enableAudio: false,
    );
  }

  Future<void> _initializeCamera() async {
    final lifecycle = WidgetsBinding.instance.lifecycleState;
    if (lifecycle != null && lifecycle != AppLifecycleState.resumed) return;
    try {
      final camera = await _cameraSession.resume();
      if (!mounted || camera == null || !identical(camera, _cameraController))
        return;
      setState(() {
        _isCameraInitialized = true;
        if (_screenState == NavigationScreenState.initializing) {
          _screenState = NavigationScreenState.readyToLocalize;
          _statusMessage = '请站在已录制的节点附近，点击“开始定位”';
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isCameraInitialized = false;
        _statusMessage = '相机初始化失败，请返回后重新进入导航。';
      });
      _onError('相机初始化失败：$e');
    }
  }

  Future<void> _disposeCamera() {
    // suspend() detaches synchronously; an older dispose cannot clear a new one.
    final pending = _cameraSession.suspend();
    _isCameraInitialized = false;
    if (mounted) setState(() {});
    return pending;
  }

  Future<void> _startLocalizationProcess() async {
    // Directly start enhanced localization without intermediate step
    await _startEnhancedLocalization();
  }

  Future<void> _loadAvailableRoutes() async {
    if (_currentLocation == null) return;

    setState(() {
      _statusMessage = '正在加载可用路线…';
    });

    try {
      final routes = await _localizationService
          .getAvailableRoutes(_currentLocation!.nodeId)
          .timeout(const Duration(seconds: 30));
      if (!mounted || _cancelLocalization) return;

      setState(() {
        _availableRoutes = routes;
        _screenState = NavigationScreenState.selectingRoute;
        _statusMessage =
            routes.isNotEmpty
                ? '找到 ${routes.length} 条可用路线'
                : '当前位置没有可用路线，请确认已从此节点录制路线。';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _screenState = NavigationScreenState.readyToLocalize);
      _onError('加载路线失败： $e');
    }
  }

  void _selectRoute(NavigationRoute route) {
    setState(() {
      _selectedRoute = route;
      _screenState = NavigationScreenState.confirmingRoute;
      _statusMessage = '已选择路线： ${route.pathName}';
    });
    _prompts.show('已选择前往${route.endNodeName}的路线，请点击开始导航。');
  }

  Future<void> _startNavigation() async {
    if (_selectedRoute == null) return;
    _prompts.reset();

    // Sync YOLO state with service before starting navigation
    _navigationService.setDisableYolo(_disableYolo);

    setState(() {
      _currentInstruction = null;
      _screenState = NavigationScreenState.navigating;
      _statusMessage = '正在启动导航…';
      // Clear localization result when navigation starts
      _localizationResult = null;
      _showLocalizationResult = false;
      // Show debug overlay when navigation starts
      _isDebugVisible = false;
      // Update debug info for navigation start
      _debugInfo = '''
🚀 NAVIGATION STARTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 Destination: ${_selectedRoute!.endNodeName}
📍 Route: ${_selectedRoute!.pathName}
📊 Total 采样点：${_selectedRoute!.waypoints.length}
🧭 Starting orientation phase...
🎯 YOLO Detection: ${_disableYolo ? 'DISABLED' : 'ENABLED'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
''';
    });

    final start = _navigationService.startNavigation(_selectedRoute!);
    final generation = _navigationService.sessionGeneration;
    await start;
    if (!mounted || generation != _navigationService.sessionGeneration) return;

    // Start periodic frame processing for navigation
    _frameProcessingTimer?.cancel();
    _frameProcessingTimer = Timer.periodic(Duration(milliseconds: 250), (_) {
      _processNavigationFrame();
    });
  }

  Future<void> _processNavigationFrame() async {
    if (_isProcessingFrame ||
        _cameraController == null ||
        !_cameraController!.value.isInitialized ||
        _screenState != NavigationScreenState.navigating ||
        _isTakingPicture ||
        _navigationService.state != nav_service.NavigationState.navigating) {
      // ✅ Only check camera access, not recovery processing
      // Debug camera readiness for voice navigation troubleshooting
      if (_screenState == NavigationScreenState.navigating) {
        print(
          '🎯 Frame processing skipped - ADMIN: camera=${_cameraController != null}, initialized=${_cameraController?.value.isInitialized}, processing=$_isProcessingFrame, taking=$_isTakingPicture',
        );
      }
      return;
    }

    _isProcessingFrame = true;
    final generation = _navigationService.sessionGeneration;
    File? imageFile;

    try {
      // Phase 1: Camera access (needs mutual exclusion)
      _isTakingPicture = true;
      final captureTimer = Stopwatch()..start();
      final image = await _cameraController!.takePicture();
      captureTimer.stop();
      imageFile = File(image.path);
      if (!mounted || generation != _navigationService.sessionGeneration) {
        return;
      }

      _isTakingPicture = false; // Camera is now free

      // Phase 2: Processing (can happen in parallel)
      await _navigationService.processNavigationFrame(
        imageFile,
        captureMs: captureTimer.elapsedMilliseconds,
      );
    } catch (e) {
      print('Error processing navigation frame: $e');
      if (mounted && generation == _navigationService.sessionGeneration) {
        _prompts.show(
          '相机拍摄已中断，请停下并保持 NAVI 在前台；恢复画面后继续。',
          kind: NavigationPromptKind.warning,
        );
      }
      _isTakingPicture = false; // Ensure camera is freed on error
    } finally {
      _isTakingPicture = false;
      if (imageFile != null) {
        try {
          await imageFile.delete();
        } catch (_) {}
      }
      _isProcessingFrame = false;
    }
  }

  // Callback methods
  void _onNavigationStateChanged(nav_service.NavigationState state) {
    if (!mounted) return;
    // Handle recovery state changes and orientation mode
    setState(() {
      switch (state) {
        case nav_service.NavigationState.initialOrientation:
          _currentInstruction = null;
          _isInOrientationMode = true;
          _isInRecoveryMode = false;
          // Ensure screen is in navigating state for orientation
          _screenState = NavigationScreenState.navigating;
          // Get target heading from navigation service
          _updateCompassData();
          break;
        case nav_service.NavigationState.awaitingManualCapture:
          _isInRecoveryMode = true;
          _isInOrientationMode = false;
          _isCapturingRecoveryFrame =
              false; // ✅ Reset when entering recovery mode
          _isTakingPicture = false;
          break;
        case nav_service.NavigationState.manualCaptureInProgress:
          _isInRecoveryMode = true;
          _isInOrientationMode = false;
          _isCapturingRecoveryFrame = false; // ✅ Reset when ready to capture
          _isTakingPicture = false;
          break;
        case nav_service.NavigationState.analyzingCapturedFrames:
          _isInRecoveryMode = true;
          _isInOrientationMode = false;
          break;
        case nav_service.NavigationState.navigating:
          _isInRecoveryMode = false;
          _isInOrientationMode = false;
          _recoveryFrameCount = 0;
          _isCapturingRecoveryFrame = false;
          _isTakingPicture = false; // Reset camera state

          // Ensure screen is in navigating state for voice-activated navigation
          _screenState = NavigationScreenState.navigating;

          // CRITICAL FIX: Always start frame processing timer when entering actual navigation
          // This fixes the issue where voice-initiated navigation doesn't work after orientation
          if (_frameProcessingTimer == null ||
              !_frameProcessingTimer!.isActive) {
            print(
              '🎯 Starting frame processing timer for navigation (voice or manual) - ADMIN',
            );
            _frameProcessingTimer = Timer.periodic(
              Duration(milliseconds: 250),
              (_) {
                _processNavigationFrame();
              },
            );
          }

          // Ensure camera is ready for voice-initiated navigation
          if (!_isCameraInitialized) {
            print(
              '🎥 Camera not ready during voice navigation - reinitializing... - ADMIN',
            );
            _initializeCamera();
          }
          break;
        case nav_service.NavigationState.idle:
        case nav_service.NavigationState.destinationReached:
          _currentInstruction = null;
          _isInRecoveryMode = false;
          _isInOrientationMode = false;
          _recoveryFrameCount = 0;
          _isCapturingRecoveryFrame = false;
          _isTakingPicture = false; // Reset camera state

          // Stop frame processing timer when navigation ends
          _frameProcessingTimer?.cancel();
          _frameProcessingTimer = null;
          print('🛑 Frame processing timer stopped - navigation ended - ADMIN');
          break;
        default:
          break;
      }
    });
  }

  void _onStatusUpdate(String message) {
    if (!mounted) return;
    setState(() {
      _statusMessage = message;
      // Don't override debug info with status messages
      // Debug info should maintain the detailed navigation service information
    });
    if (message.contains('图像处理失败')) {
      _prompts.show(
        '暂时无法获取导航画面，请停下并检查服务器连接。',
        kind: NavigationPromptKind.warning,
      );
    }

    // Handle voice assistant commands that need to trigger actions
    if (message == 'Enhanced localization requested') {
      _handleVoiceLocalize();
    }
  }

  void _onInstructionUpdate(nav_service.NavigationInstruction instruction) {
    if (!mounted) return;
    setState(() {
      _currentInstruction = instruction;
    });
    _onNavigationGuidance(instruction.spokenInstruction, announce: false);
  }

  void _onError(String error) {
    if (!mounted) return;
    setState(() {
      _statusMessage = '操作未完成：$error';
    });
    _prompts.show(
      error.contains('相机') ||
              error.contains('capture') ||
              error.contains('Camera')
          ? '相机暂不可用，请保持 NAVI 在前台，返回导航页后重试。'
          : '操作未完成，请停下，检查屏幕提示和服务器连接后重试。',
      kind: NavigationPromptKind.warning,
    );

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(error),
        backgroundColor: Colors.red,
        duration: Duration(seconds: 5),
      ),
    );
  }

  void _onNavigationGuidance(String message, {bool announce = true}) {
    if (!mounted) return;
    final state = _navigationService.state;
    final kind =
        state == nav_service.NavigationState.destinationReached
            ? NavigationPromptKind.arrival
            : state == nav_service.NavigationState.initialOrientation
            ? NavigationPromptKind.orientation
            : message.contains('停下') ||
                message.contains('失败') ||
                message.contains('无法')
            ? NavigationPromptKind.warning
            : message.contains('左转') || message.contains('右转')
            ? NavigationPromptKind.turn
            : NavigationPromptKind.instruction;
    _prompts.show(message, kind: kind, announce: announce);
  }

  void _onRequestManualCapture() {
    setState(() {
      _isInRecoveryMode = true;
      _recoveryFrameCount = 0;
      _isCapturingRecoveryFrame =
          false; // ✅ Reset capture state when recovery starts/restarts
      _isTakingPicture = false; // ✅ Reset camera state
    });
  }

  void _onManualFrameCaptured(File imageFile) {
    setState(() {
      _recoveryFrameCount++;
    });
  }

  void _onDebugUpdate(String debugInfo) {
    setState(() {
      // Preserve the detailed debug information from navigation service
      // This contains waypoint matching details, similarity scores, etc.
      _debugInfo = debugInfo;
    });
  }

  void _updateCompassData() {
    // Get current and target headings from navigation service
    final currentRoute = _navigationService.currentRoute;
    if (currentRoute != null && currentRoute.waypoints.isNotEmpty) {
      // Get first waypoint heading as target
      final firstWaypoint = currentRoute.waypoints.firstWhere(
        (waypoint) => waypoint.sequenceNumber == 0,
        orElse: () => currentRoute.waypoints.first,
      );
      _targetHeading = firstWaypoint.heading;
    }

    // Get current compass heading
    _currentHeading = _navigationService.currentHeading;

    // Start periodic compass updates during orientation
    if (_isInOrientationMode) {
      Timer.periodic(Duration(milliseconds: 500), (timer) {
        if (!_isInOrientationMode) {
          timer.cancel();
          return;
        }
        setState(() {
          _currentHeading = _navigationService.currentHeading;
        });
      });
    }
  }

  bool _isWithinOrientationRange() {
    if (_currentHeading == null || _targetHeading == null) return false;

    return isDepartureHeadingAligned(_currentHeading!, _targetHeading!);
  }

  Future<void> _captureRecoveryFrame() async {
    if (!_isInRecoveryMode ||
        _cameraController == null ||
        !_cameraController!.value.isInitialized ||
        _isCapturingRecoveryFrame ||
        _isTakingPicture) {
      // ✅ Only check camera access, not frame processing
      return;
    }

    setState(() {
      _isCapturingRecoveryFrame = true;
    });

    try {
      // Phase 1: Camera access (needs mutual exclusion)
      _isTakingPicture = true;
      final image = await _cameraController!.takePicture();
      _isTakingPicture = false; // Camera is now free

      // Phase 2: Processing (can happen in parallel)
      final imageFile = File(image.path);
      await _navigationService.processManualCapturedFrame(imageFile);
    } catch (e) {
      _onError('Failed to capture recovery frame: $e');
      _isTakingPicture = false; // Ensure camera is freed on error
    } finally {
      if (mounted) {
        setState(() {
          _isCapturingRecoveryFrame = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Stack(
          children: [
            // Full screen camera preview
            _buildCameraPreview(),
            if (_screenState == NavigationScreenState.readyToLocalize)
              Positioned(
                top: 4,
                left: 8,
                child: FilledButton.icon(
                  icon: const Icon(Icons.troubleshoot),
                  label: const Text('定位诊断'),
                  onPressed:
                      () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => const LocalizationDiagnosticsScreen(),
                        ),
                      ),
                ),
              ),

            // Localization result display at top - hide during navigation
            if (_showLocalizationResult &&
                _screenState != NavigationScreenState.navigating)
              Positioned(
                top:
                    50, // Moved down to avoid overlapping with voice status indicator
                left: 0,
                right: 0,
                child: _buildLocalizationResultDisplay(),
              ),

            // Voice assistant status indicator
            if (_isVoiceInitialized)
              Positioned(
                top: 16,
                right: 16,
                child: _buildVoiceStatusIndicator(),
              ),

            // Debug overlay at top (admin only)
            if (_screenState == NavigationScreenState.navigating &&
                !_isInOrientationMode)
              DebugOverlay(
                debugInfo: _debugInfo,
                isVisible: _isDebugVisible, // Use the debug visibility state
                onToggle: () {
                  setState(() {
                    _isDebugVisible = !_isDebugVisible;
                  });
                },
              ),

            // Overlay control panel at bottom
            Positioned(
              left: 0,
              right: 0,
              bottom: 0,
              child: _buildOverlayControlPanel(),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildCameraPreview() {
    if (!_isCameraInitialized || _cameraController == null) {
      return Container(
        color: Colors.black,
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              CircularProgressIndicator(color: Colors.white),
              SizedBox(height: 16),
              Text(
                '正在初始化相机…',
                style: TextStyle(color: Colors.white, fontSize: 16),
              ),
            ],
          ),
        ),
      );
    }

    return SizedBox.expand(
      child: Stack(
        children: [
          CameraPreview(_cameraController!),

          // Compass overlay during orientation phase
          if (_isInOrientationMode &&
              _currentHeading != null &&
              _targetHeading != null)
            Positioned(
              top: 60,
              left: 0,
              right: 0,
              child: Center(
                child: CompassWidget(
                  currentHeading: _currentHeading!,
                  targetHeading: _targetHeading!,
                  threshold: departureHeadingTolerance,
                  isWithinRange: _isWithinOrientationRange(),
                  size: 220.0, // Reduced size to fit better
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildLocalizationResultDisplay() {
    return Container(
      padding: EdgeInsets.all(16),
      child: SafeArea(
        child: Row(
          children: [
            Expanded(
              child: Container(
                padding: EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.black.withValues(alpha: 0.4),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: Colors.white.withValues(alpha: 0.1),
                    width: 1,
                  ),
                ),
                child: Text(
                  _localizationResult ?? '正在处理…',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                  ),
                  textAlign: TextAlign.center,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildOverlayControlPanel() {
    return NavigationPromptLayout(
      prompts: _prompts,
      actions: _buildActionButtons(),
      details: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Screen content
          if (_isInOrientationMode ||
              _screenState == NavigationScreenState.readyToLocalize ||
              _screenState == NavigationScreenState.processingLocation ||
              _screenState == NavigationScreenState.confirmingRoute)
            NavigationContentFrame(
              orientation: _isInOrientationMode,
              child:
                  _isInOrientationMode
                      ? _buildOrientationContent()
                      : _buildScreenContent(),
            )
          else if (_screenState != NavigationScreenState.navigating ||
              _navigationService.state ==
                  nav_service.NavigationState.destinationReached)
            SizedBox(
              height: MediaQuery.of(context).size.height * 0.3,
              child: _buildScreenContent(),
            ),

          SizedBox(height: 12),

          // Navigation instruction OR Recovery mode (only show one, not both)
          if (_screenState == NavigationScreenState.navigating &&
              !_isInOrientationMode) ...[
            if (_isInRecoveryMode)
              // Recovery mode replaces navigation instruction
              Container(
                margin: EdgeInsets.only(bottom: 12),
                padding: EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.8),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.3),
                    width: 1,
                  ),
                ),
                child: Column(
                  children: [
                    Row(
                      children: [
                        Icon(Icons.refresh, color: Colors.white, size: 20),
                        SizedBox(width: 8),
                        Text(
                          '正在重新定位',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                    SizedBox(height: 8),
                    Text(
                      '恢复画面 ${_recoveryFrameCount} / 3',
                      style: TextStyle(color: Colors.white70, fontSize: 14),
                    ),
                    if (_navigationService.state ==
                        nav_service
                            .NavigationState
                            .manualCaptureInProgress) ...[
                      SizedBox(height: 12),
                      ElevatedButton.icon(
                        onPressed:
                            (_isCapturingRecoveryFrame || _isTakingPicture)
                                ? null
                                : _captureRecoveryFrame,
                        icon:
                            (_isCapturingRecoveryFrame || _isTakingPicture)
                                ? SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                    color: Colors.black,
                                    strokeWidth: 2,
                                  ),
                                )
                                : Icon(Icons.camera_alt, color: Colors.black),
                        label: Text(
                          (_isCapturingRecoveryFrame || _isTakingPicture)
                              ? '正在拍摄…'
                              : '拍摄画面',
                          style: TextStyle(color: Colors.black),
                        ),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.white,
                          foregroundColor: Colors.black,
                        ),
                      ),
                    ],
                  ],
                ),
              )
            else if (_navigationService.currentRoute != null &&
                _navigationService.state !=
                    nav_service.NavigationState.destinationReached)
              NavigationProgressPanel(
                completed: _navigationService.currentWaypointIndex,
                total: _navigationService.currentRoute!.waypoints.length,
                status: _navigationService.matchingStatus,
                motionHint: _navigationService.motionHint,
                onConfirmMovement:
                    _navigationService.needsManualMovement
                        ? _navigationService.confirmMovement
                        : null,
                instruction: _currentInstruction?.displayText ?? '等待路线画面匹配',
              ),
          ],
        ],
      ),
    );
  }

  Widget _buildScreenContent() {
    switch (_screenState) {
      case NavigationScreenState.initializing:
        return _buildInitializingContent();
      case NavigationScreenState.readyToLocalize:
        return _buildReadyToLocalizeContent();
      case NavigationScreenState.processingLocation:
        return _buildProcessingLocationContent();
      case NavigationScreenState.selectingRoute:
        return _buildRouteSelectionContent();
      case NavigationScreenState.confirmingRoute:
        return _buildRouteConfirmationContent();
      case NavigationScreenState.navigating:
        return _buildNavigationContent();
    }
  }

  Widget _buildOrientationContent() {
    if (!_isInOrientationMode ||
        _currentHeading == null ||
        _targetHeading == null) {
      return Container(
        padding: EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Colors.black.withOpacity(0.6),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,

          children: [
            CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
            SizedBox(height: 8),
            Text(
              '正在等待指南针…',
              style: TextStyle(
                color: Colors.white,
                fontSize: 14,
                fontWeight: FontWeight.bold,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    }

    return Container(
      padding: EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.6),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,

        children: [
          Text(
            _isWithinOrientationRange() ? '✅ 出发朝向已对准' : '🧭 正在校准出发方向',
            style: TextStyle(
              color: _isWithinOrientationRange() ? Colors.green : Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.bold,
            ),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 8),
          const Text(
            '请站在起点调整朝向，此时尚未开始路线导航。\n白色箭头是手机前方，蓝点是出发方向；对准后圆盘会消失。',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white, fontSize: 13),
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              Column(
                children: [
                  Text(
                    '当前朝向',
                    style: TextStyle(color: Colors.white70, fontSize: 11),
                  ),
                  Text(
                    '${_currentHeading!.toStringAsFixed(0)}°',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
              Text('/', style: TextStyle(color: Colors.white70, fontSize: 18)),
              Column(
                children: [
                  Text(
                    '出发朝向',
                    style: TextStyle(color: Colors.white70, fontSize: 11),
                  ),
                  Text(
                    '${_targetHeading!.toStringAsFixed(0)}°',
                    style: TextStyle(
                      color: Colors.blue,
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildInitializingContent() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          CircularProgressIndicator(),
          SizedBox(height: 12),
          Text(
            _statusMessage,
            style: TextStyle(color: Colors.white, fontSize: 14),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  Widget _buildReadyToLocalizeContent() {
    return LocalizationReadyPanel(
      status: _statusMessage,
      verifyWithGpt: _useVLMVerification,
      peopleInpainting: !_disableYolo,
      onVerificationChanged:
          (_gptApiKey?.isNotEmpty ?? false)
              ? (value) => setState(() => _useVLMVerification = value)
              : null,
      onPeopleChanged: (value) {
        setState(() => _disableYolo = !value);
        _navigationService.setDisableYolo(_disableYolo);
      },
    );
  }

  Widget _buildProcessingLocationContent() {
    return Container(
      padding: EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withOpacity(0.1), width: 1),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          // Enhanced progress indicator
          Stack(
            alignment: Alignment.center,
            children: [
              SizedBox(
                width: 60,
                height: 60,
                child: CircularProgressIndicator(
                  strokeWidth: 3,
                  valueColor: AlwaysStoppedAnimation<Color>(Colors.blue),
                ),
              ),
              Icon(Icons.auto_awesome, color: Colors.white, size: 24),
            ],
          ),
          SizedBox(height: 16),
          Text(
            '正在定位',
            style: TextStyle(
              color: Colors.white,
              fontSize: 20,
              fontWeight: FontWeight.bold,
            ),
            textAlign: TextAlign.center,
          ),
          SizedBox(height: 12),
          Text(
            _statusMessage,
            style: TextStyle(color: Colors.white70, fontSize: 14),
            textAlign: TextAlign.center,
          ),
          SizedBox(height: 16),

          // VLM status indicator
          if (_useVLMVerification && _gptApiKey != null)
            Container(
              padding: EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: Colors.green.withOpacity(0.2),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: Colors.green.withOpacity(0.3)),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.psychology, color: Colors.green, size: 16),
                  SizedBox(width: 6),
                  Text(
                    'GPT 辅助验证已开启',
                    style: TextStyle(color: Colors.green, fontSize: 12),
                  ),
                ],
              ),
            ),

          if (!_useVLMVerification || _gptApiKey == null)
            Container(
              padding: EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: Colors.orange.withOpacity(0.2),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: Colors.orange.withOpacity(0.3)),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.speed, color: Colors.orange, size: 16),
                  SizedBox(width: 6),
                  Text(
                    'DINOv2 视觉定位模式',
                    style: TextStyle(color: Colors.orange, fontSize: 12),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildRouteSelectionContent() {
    return Container(
      padding: EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withOpacity(0.1), width: 1),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.route, color: Colors.blue, size: 24),
              SizedBox(width: 8),
              Text(
                '可用路线',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
          SizedBox(height: 12),

          Expanded(
            child:
                _availableRoutes.isEmpty
                    ? Center(
                      child: Text(
                        '当前位置没有可用路线，请确认已从此节点录制路线。',
                        style: TextStyle(color: Colors.white70, fontSize: 16),
                      ),
                    )
                    : ListView.builder(
                      itemCount: _availableRoutes.length,
                      itemBuilder: (context, index) {
                        final route = _availableRoutes[index];
                        return Card(
                          color: Colors.grey[800],
                          margin: EdgeInsets.only(bottom: 8),
                          child: ListTile(
                            leading: Icon(
                              Icons.directions,
                              color: Colors.green,
                              size: 24,
                            ),
                            title: Text(
                              route.endNodeName,
                              style: TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            subtitle: Text(
                              route.pathName,
                              style: TextStyle(color: Colors.white70),
                            ),
                            trailing: Icon(
                              Icons.arrow_forward_ios,
                              color: Colors.white,
                            ),
                            onTap: () => _selectRoute(route),
                          ),
                        );
                      },
                    ),
          ),
        ],
      ),
    );
  }

  Widget _buildRouteConfirmationContent() {
    if (_selectedRoute == null) return Container();

    return Container(
      padding: EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.4),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withOpacity(0.1), width: 1),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.check_circle, color: Colors.green, size: 24),
              SizedBox(width: 8),
              Text(
                'Confirm Route',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
          SizedBox(height: 16),

          Card(
            color: Colors.grey[800],
            child: Padding(
              padding: EdgeInsets.all(16),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(Icons.location_on, color: Colors.red, size: 20),
                      SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Destination: ${_selectedRoute!.endNodeName}',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                    ],
                  ),
                  SizedBox(height: 12),
                  Row(
                    children: [
                      Icon(Icons.route, color: Colors.blue, size: 20),
                      SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Route: ${_selectedRoute!.pathName}',
                          style: TextStyle(color: Colors.white70, fontSize: 14),
                        ),
                      ),
                    ],
                  ),
                  SizedBox(height: 12),
                  Row(
                    children: [
                      Icon(Icons.flag, color: Colors.orange, size: 20),
                      SizedBox(width: 8),
                      Text(
                        '${_selectedRoute!.waypoints.length} 个采样点',
                        style: TextStyle(color: Colors.orange, fontSize: 14),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNavigationContent() {
    if (_navigationService.state ==
        nav_service.NavigationState.destinationReached) {
      final route = _navigationService.currentRoute!;
      return NavigationArrivalPanel(
        destination: route.endNodeName,
        total: route.waypoints.length,
      );
    }
    return Container(); // Navigation controls moved to action buttons
  }

  Widget _buildActionButtons() {
    switch (_screenState) {
      case NavigationScreenState.initializing:
        return SizedBox.shrink();

      case NavigationScreenState.readyToLocalize:
        return ElevatedButton.icon(
          onPressed: _isCameraInitialized ? _startLocalizationProcess : null,
          icon: Icon(Icons.location_searching, size: 24),
          label: Text('开始定位'),
          style: ElevatedButton.styleFrom(
            backgroundColor: Colors.blue,
            foregroundColor: Colors.white,
            minimumSize: Size(double.infinity, 50),
            textStyle: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
        );

      case NavigationScreenState.processingLocation:
        return OutlinedButton.icon(
          onPressed:
              _cancelLocalization
                  ? null
                  : () {
                    setState(() {
                      _cancelLocalization = true;
                      _statusMessage = '正在取消定位，等待当前请求结束…';
                    });
                  },
          icon: const Icon(Icons.close),
          label: Text(_cancelLocalization ? '正在取消…' : '取消定位'),
          style: OutlinedButton.styleFrom(foregroundColor: Colors.white),
        );

      case NavigationScreenState.selectingRoute:
        return ElevatedButton.icon(
          onPressed: _startLocalizationProcess,
          icon: Icon(Icons.refresh, size: 20),
          label: Text('重新定位'),
          style: ElevatedButton.styleFrom(
            backgroundColor: Colors.grey[700],
            foregroundColor: Colors.white,
            minimumSize: Size(double.infinity, 48),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
        );

      case NavigationScreenState.confirmingRoute:
        return Row(
          children: [
            Expanded(
              child: ElevatedButton.icon(
                onPressed: () {
                  setState(() {
                    _screenState = NavigationScreenState.selectingRoute;
                    _selectedRoute = null;
                  });
                },
                icon: Icon(Icons.arrow_back, size: 20),
                label: Text('返回'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.grey[700],
                  foregroundColor: Colors.white,
                  minimumSize: Size(0, 48),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
            SizedBox(width: 12),
            Expanded(
              child: ElevatedButton.icon(
                onPressed: _startNavigation,
                icon: Icon(Icons.navigation, size: 20),
                label: Text('开始导航'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.green,
                  foregroundColor: Colors.white,
                  minimumSize: Size(0, 48),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
          ],
        );

      case NavigationScreenState.navigating:
        if (_navigationService.state ==
            nav_service.NavigationState.destinationReached) {
          return ElevatedButton.icon(
            onPressed: _stopAndReturnToMain,
            icon: const Icon(Icons.check),
            label: const Text('结束并返回'),
            style: ElevatedButton.styleFrom(
              backgroundColor: Colors.green,
              foregroundColor: Colors.white,
              minimumSize: const Size(double.infinity, 50),
            ),
          );
        }
        return Row(
          children: [
            Expanded(
              child: ElevatedButton.icon(
                onPressed: _restartNavigationFromBeginning,
                icon: Icon(Icons.refresh, size: 20),
                label: Text('回起点重启'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.blue,
                  foregroundColor: Colors.white,
                  minimumSize: Size(0, 48),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
            SizedBox(width: 12),
            Expanded(
              child: ElevatedButton.icon(
                onPressed: _stopAndReturnToMain,
                icon: Icon(Icons.stop, size: 20),
                label: Text('停止导航'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: Colors.red,
                  foregroundColor: Colors.white,
                  minimumSize: Size(0, 48),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
          ],
        );
    }
  }

  /// Start enhanced localization with automatic 8-second scanning
  Future<void> _startEnhancedLocalization() async {
    if (_localizationBusy || !mounted) return;
    if (!_isCameraInitialized || _cameraController == null) {
      _onError('相机尚未就绪，请返回后重新进入导航。');
      return;
    }
    _localizationBusy = true;
    _cancelLocalization = false;
    setState(() {
      _screenState = NavigationScreenState.processingLocation;
      _statusMessage = '正在启动定位，请缓慢转动手机扫描周围环境…';
      _showLocalizationResult = false;
    });
    try {
      await _processCapturedDirections();
    } finally {
      _localizationBusy = false;
      if (mounted && _cancelLocalization) {
        setState(() {
          _screenState = NavigationScreenState.readyToLocalize;
          _statusMessage = '定位拍照已中断，请保持页面在前台，点击开始定位重试。';
        });
        _prompts.show(
          '定位已中断，请保持页面在前台，点击开始定位重试。',
          kind: NavigationPromptKind.warning,
        );
      }
    }
  }

  Future<void> _processCapturedDirections() async {
    final camera = _cameraController;
    final cameraGeneration = _cameraSession.generation;
    try {
      if (camera == null) throw Exception('相机尚未恢复，请稍后重试');
      // Use enhanced localization with automatic scanning + GPT verification
      final result = await _clipService.performEnhancedLocalization(
        cameraController: camera,
        gptApiKey: _useVLMVerification ? _gptApiKey : null,
        disableYolo: _disableYolo,
        isCancelled:
            () =>
                !mounted ||
                _cancelLocalization ||
                cameraGeneration != _cameraSession.generation ||
                !identical(camera, _cameraController),
        onStatusUpdate: (message) async {
          if (!mounted || _cancelLocalization) return;
          setState(() => _statusMessage = message);

          if (message.contains('正在扫描')) {
            _prompts.show(
              '正在定位，请站稳并缓慢转动手机，扫描周围环境。',
              kind: NavigationPromptKind.progress,
            );
          } else if (message.contains('正在拍摄') || message.contains('正在提取')) {
            _prompts.show(
              message,
              kind: NavigationPromptKind.progress,
              announce: false,
            );
          } else if (message.contains('正在读取')) {
            _prompts.show(
              '扫描完成，正在匹配当前位置，请稍候。',
              kind: NavigationPromptKind.progress,
            );
          } else if (message.contains('正在确认') ||
              message.contains('VLM verification')) {
            _prompts.show('正在确认当前位置，请稍候。', kind: NavigationPromptKind.progress);
          }
        },
      );

      if (!mounted || _cancelLocalization) return;
      if (result.success && result.detectedLocation != null) {
        setState(() {
          _currentLocation = LocationMatch(
            nodeId:
                result.nodeId ??
                result
                    .detectedLocation!, // Use actual nodeId, fallback to place name
            nodeName: result.detectedLocation!,
            similarity: result.confidence ?? 0.0,
            mapId:
                'enhanced_localization', // Placeholder for enhanced localization
          );

          // Build simple result string - just location with confidence
          String resultText = '📍 ${result.detectedLocation}';
          if (result.confidence != null) {
            resultText += '（匹配度 ${(result.confidence! * 100).round()}%）';
          }

          _localizationResult = resultText;
          _showLocalizationResult = true;

          // Store result for voice assistant explanations
          _voiceAssistant.updateLastLocalizationResult({
            'detectedLocation': result.detectedLocation,
            'nodeId': result.nodeId,
            'confidence': result.confidence,
            'embeddingSimilarity': result.embeddingSimilarity,
            'vlmConfidence': result.vlmConfidence,
            'vlmReasoning': result.vlmReasoning,
          });
        });

        _prompts.show('定位成功，当前位置是${result.detectedLocation}。请选择目的地。');
      } else {
        setState(() {
          _screenState = NavigationScreenState.readyToLocalize;
          _statusMessage = result.errorMessage ?? '未能识别当前位置，请回到已录制节点附近重试。';
          _localizationResult =
              '❌ ${result.errorMessage ?? 'No location found'}';
          _showLocalizationResult = true;
        });

        _prompts.show(
          result.errorMessage?.contains('相机') == true
              ? '相机拍照已中断，请返回导航页后重新定位。'
              : result.errorMessage?.contains('图片特征') == true
              ? '定位连接失败，请检查服务器连接后重试。'
              : '暂未识别当前位置，请核对位置与拍摄方向，再重新定位。',
          kind: NavigationPromptKind.warning,
        );
      }

      // Load available routes if location was found
      if (result.success && result.detectedLocation != null) {
        await _loadAvailableRoutes();
      }
    } catch (e) {
      if (!mounted || _cancelLocalization) return;
      _onError('定位失败： $e');
      setState(() {
        _screenState = NavigationScreenState.readyToLocalize;
        _localizationResult = '❌ 定位失败，请检查连接后重试';
        _showLocalizationResult = true;
      });
    }
  }

  /// Build voice assistant status indicator
  Widget _buildVoiceStatusIndicator() {
    Color statusColor;
    IconData statusIcon;
    String statusText;

    switch (_voiceState) {
      case VoiceSessionState.idle:
        statusColor = Colors.green;
        statusIcon = Icons.mic;
        statusText = '等待语音唤醒';
        break;
      case VoiceSessionState.wakeWordDetected:
        statusColor = Colors.orange;
        statusIcon = Icons.hearing;
        statusText = '已听到唤醒词';
        break;
      case VoiceSessionState.listening:
        statusColor = Colors.blue;
        statusIcon = Icons.mic;
        statusText = '正在聆听…';
        break;
      case VoiceSessionState.processing:
        statusColor = Colors.purple;
        statusIcon = Icons.psychology;
        statusText = '正在处理指令';
        break;
      case VoiceSessionState.executing:
        statusColor = Colors.indigo;
        statusIcon = Icons.play_arrow;
        statusText = '正在执行…';
        break;
      case VoiceSessionState.speaking:
        statusColor = Colors.cyan;
        statusIcon = Icons.volume_up;
        statusText = '正在播报…';
        break;
      case VoiceSessionState.error:
        statusColor = Colors.red;
        statusIcon = Icons.error;
        statusText = '语音功能暂不可用';
        break;
    }

    return Container(
      padding: EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: statusColor.withOpacity(0.1),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: statusColor.withOpacity(0.3)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(statusIcon, color: statusColor, size: 16),
          SizedBox(width: 6),
          Text(
            statusText,
            style: TextStyle(
              color: statusColor,
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _stopAndReturnToMain() async {
    _prompts.reset();
    await _navigationService.stopNavigation();

    // Ensure frame processing timer is stopped
    _frameProcessingTimer?.cancel();
    _frameProcessingTimer = null;

    setState(() {
      _screenState = NavigationScreenState.readyToLocalize;
      _selectedRoute = null;
      _currentLocation = null;
      _availableRoutes = [];
      _currentInstruction = null;
      _statusMessage = '请站在已录制的节点附近，点击“开始定位”';
      // Reset debug info to initial state
      _debugInfo = '''
🐛 DEBUG MODE - NAVIGATION SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Current State: Ready to localize
🎯 Navigation Service: Ready
⏳ Waiting for user to start localization...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
''';
    });
  }

  Future<void> _restartNavigationFromBeginning() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder:
          (context) => AlertDialog(
            title: const Text('从路线起点重新导航？'),
            content: const Text('这会清空本次路线识别进度。请先回到录制路线的起点，再重新校准出发方向。'),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('取消'),
              ),
              TextButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('已回到起点，重启'),
              ),
            ],
          ),
    );
    if (confirmed != true || !mounted) return;

    if (_selectedRoute != null) {
      _prompts.reset();
      await _navigationService.stopNavigation();

      // Sync YOLO state with service before restarting navigation
      _navigationService.setDisableYolo(_disableYolo);

      setState(() {
        // Update debug info for navigation restart
        _debugInfo = '''
🔄 NAVIGATION RESTARTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 Destination: ${_selectedRoute!.endNodeName}
📍 Route: ${_selectedRoute!.pathName}
📊 Total 采样点：${_selectedRoute!.waypoints.length}
🧭 Restarting orientation phase...
🎯 YOLO Detection: ${_disableYolo ? 'DISABLED' : 'ENABLED'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
''';
      });

      await _navigationService.startNavigation(_selectedRoute!);
      // Optionally reset any progress if the service supports it
    }
  }
}

enum NavigationScreenState {
  initializing,
  readyToLocalize,
  processingLocation,
  selectingRoute,
  confirmingRoute,
  navigating,
}
