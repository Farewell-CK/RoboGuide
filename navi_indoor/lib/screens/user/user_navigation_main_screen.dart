import '../../widgets/navigation_progress_panel.dart';
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
import '../../services/navigation_feedback.dart';

class UserNavigationMainScreen extends StatefulWidget {
  final CameraDescription camera;

  const UserNavigationMainScreen({Key? key, required this.camera})
    : super(key: key);

  @override
  State<UserNavigationMainScreen> createState() =>
      _UserNavigationMainScreenState();
}

class _UserNavigationMainScreenState extends State<UserNavigationMainScreen>
    with WidgetsBindingObserver {
  // Services
  late ClipService _clipService;
  late SupabaseService _supabaseService;
  late PositionLocalizationService _localizationService;
  late nav_service.RealTimeNavigationService _navigationService;
  late VoiceAssistantService _voiceAssistant;

  // TTS for location announcements
  late FlutterTts _flutterTts;

  // Camera
  CameraController? _cameraController;
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
    _disposeCamera(); // Use the safe disposal method
    _navigationService.dispose();
    _localizationService.dispose();
    _voiceAssistant.dispose();
    _flutterTts.stop(); // Stop any ongoing TTS
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      // Dispose camera when app goes to background
      _disposeCamera();
    } else if (state == AppLifecycleState.resumed) {
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
      _navigationService = nav_service.RealTimeNavigationService(
        clipService: _clipService,
        onStateChanged: _onNavigationStateChanged,
        onStatusUpdate: _onStatusUpdate,
        onInstructionUpdate: _onInstructionUpdate,
        onError: _onError,
        onRequestManualCapture: _onRequestManualCapture,
        onManualFrameCaptured: _onManualFrameCaptured,
      );

      // Initialize voice assistant
      _voiceAssistant = VoiceAssistantService();

      // Initialize TTS for location announcements
      _flutterTts = FlutterTts();
      unawaited(_configureSpeech());

      // Initialize camera
      await _initializeCamera();

      if (!mounted || !_isCameraInitialized) return;
      setState(() {
        _screenState = NavigationScreenState.readyToLocalize;
        _statusMessage = '请站在已录制的节点附近，点击“开始定位”';
      });
      if ((dotenv.env['OPENAI_API_KEY'] ?? '').isNotEmpty &&
          (dotenv.env['PICOVOICE_ACCESS_KEY'] ?? '').isNotEmpty) {
        unawaited(_initializeVoiceAssistant());
      }
    } catch (e) {
      _onError('初始化失败： $e');
    }
  }

  Future<void> _configureSpeech() async {
    try {
      await _flutterTts
          .setLanguage('zh-CN')
          .timeout(const Duration(seconds: 2));
      await _flutterTts.setSpeechRate(0.5).timeout(const Duration(seconds: 2));
      await _flutterTts.setVolume(0.9).timeout(const Duration(seconds: 2));
    } catch (e) {
      debugPrint('语音初始化失败，仍可正常使用按钮：$e');
    }
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
      _voiceAssistant = VoiceAssistantService();

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

  Future<void> _initializeCamera() async {
    try {
      // Dispose existing controller if it exists and is disposed
      await _disposeCamera();

      // Request permissions including step counter
      final permissions =
          await [
            Permission.camera,
            Permission.microphone, // For voice assistant
            Permission.activityRecognition, // For step counter
            Permission.sensors, // For step counter and compass
          ].request();

      if (permissions[Permission.camera] != PermissionStatus.granted) {
        throw Exception('相机权限未开启，请在系统设置中允许使用相机');
      }

      // Microphone permission is optional for the camera and touch controls.

      // Check step counter permissions
      if (permissions[Permission.activityRecognition] !=
          PermissionStatus.granted) {
        print(
          '⚠️ Activity recognition permission denied - step counter may not work',
        );
      }

      _cameraController = CameraController(
        widget.camera,
        ResolutionPreset.medium,
        enableAudio: false,
      );

      await _cameraController!.initialize();

      if (mounted) {
        setState(() {
          _isCameraInitialized = true;
        });
      }
    } catch (e) {
      print('Error initializing camera: $e');
      if (mounted) {
        setState(() {
          _isCameraInitialized = false;
          _statusMessage = '相机初始化失败： ${e.toString()}';
        });
      }
      _onError('相机初始化失败： $e');
    }
  }

  Future<void> _disposeCamera() async {
    if (_cameraController != null) {
      try {
        if (_cameraController!.value.isInitialized) {
          await _cameraController!.dispose();
        }
      } catch (e) {
        print('Error disposing camera: $e');
      } finally {
        _cameraController = null;
        if (mounted) {
          setState(() {
            _isCameraInitialized = false;
          });
        }
      }
    }
  }

  Future<void> _startLocalizationProcess() async {
    // Provide immediate audio feedback when button is pressed
    try {
      speakWithoutBlocking(() => _flutterTts.speak('正在开始定位，请站稳并缓慢扫描周围环境。'));
    } catch (e) {
      print('TTS Error: $e');
    }

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
  }

  Future<void> _startNavigation() async {
    if (_selectedRoute == null) return;

    setState(() {
      _currentInstruction = null;
      _screenState = NavigationScreenState.navigating;
      _statusMessage = '正在启动导航…';
      // Clear localization result when navigation starts
      _localizationResult = null;
      _showLocalizationResult = false;
    });

    final start = _navigationService.startNavigation(_selectedRoute!);
    final generation = _navigationService.sessionGeneration;
    await start;
    if (!mounted || generation != _navigationService.sessionGeneration) return;

    // Start periodic frame processing for navigation
    _frameProcessingTimer?.cancel();
    _frameProcessingTimer = Timer.periodic(Duration(seconds: 1), (_) {
      _processNavigationFrame();
    });
  }

  Future<void> _processNavigationFrame() async {
    if (_isProcessingFrame ||
        _cameraController == null ||
        !_cameraController!.value.isInitialized ||
        _screenState != NavigationScreenState.navigating ||
        _isTakingPicture) {
      // ✅ Only check camera access, not recovery processing
      return;
    }

    _isProcessingFrame = true;
    final generation = _navigationService.sessionGeneration;

    try {
      // Phase 1: Camera access (needs mutual exclusion)
      _isTakingPicture = true;
      final image = await _cameraController!.takePicture();
      if (!mounted || generation != _navigationService.sessionGeneration) {
        await File(image.path).delete();
        return;
      }

      _isTakingPicture = false; // Camera is now free

      // Phase 2: Processing (can happen in parallel)
      final imageFile = File(image.path);
      await _navigationService.processNavigationFrame(imageFile);

      // Clean up temp file
      await imageFile.delete();
    } catch (e) {
      print('Error processing navigation frame: $e');
      _isTakingPicture = false; // Ensure camera is freed on error
    } finally {
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
          if (_screenState != NavigationScreenState.navigating) {
            _screenState = NavigationScreenState.navigating;
            // Start frame processing timer if not already running (for voice navigation)
            if (_frameProcessingTimer == null ||
                !_frameProcessingTimer!.isActive) {
              _frameProcessingTimer = Timer.periodic(Duration(seconds: 1), (_) {
                _processNavigationFrame();
              });
            }
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
    });

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
  }

  void _onError(String error) {
    if (!mounted) return;
    setState(() {
      _statusMessage = 'Error: $error';
    });

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(error),
        backgroundColor: Colors.red,
        duration: Duration(seconds: 5),
      ),
    );
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
    return Container(
      padding: EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.bottomCenter,
          end: Alignment.topCenter,
          colors: [
            Colors.black.withOpacity(0.3),
            Colors.black.withOpacity(0.1),
            Colors.transparent,
          ],
          stops: [0.0, 0.8, 1.0],
        ),
      ),
      child: Column(
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
          else
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
            else if (_navigationService.currentRoute != null)
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

          // Action buttons
          _buildActionButtons(),
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
                    'GPT-5 Verification Active',
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
          _statusMessage = '定位已取消，可重新开始。';
        });
      }
    }
  }

  Future<void> _processCapturedDirections() async {
    try {
      // Use enhanced localization with automatic scanning + GPT verification
      final result = await _clipService.performEnhancedLocalization(
        cameraController: _cameraController!,
        gptApiKey: _useVLMVerification ? _gptApiKey : null,
        disableYolo: _disableYolo,
        isCancelled: () => !mounted || _cancelLocalization,
        onStatusUpdate: (message) {
          if (!mounted || _cancelLocalization) return;
          setState(() => _statusMessage = message);
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
          _voiceAssistant?.updateLastLocalizationResult({
            'detectedLocation': result.detectedLocation,
            'nodeId': result.nodeId,
            'confidence': result.confidence,
            'embeddingSimilarity': result.embeddingSimilarity,
            'vlmConfidence': result.vlmConfidence,
            'vlmReasoning': result.vlmReasoning,
          });
        });

        // Automatically announce the location after successful localization
        String announcement = '当前位置： ${result.detectedLocation}';
        try {
          speakWithoutBlocking(() => _flutterTts.speak(announcement));
        } catch (e) {
          print('TTS Error: $e');
        }
      } else {
        setState(() {
          _screenState = NavigationScreenState.readyToLocalize;
          _statusMessage = '未能识别当前位置，请回到已录制节点附近重试。';
          _localizationResult =
              '❌ ${result.errorMessage ?? 'No location found'}';
          _showLocalizationResult = true;
        });
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
    await _navigationService.stopNavigation();
    setState(() {
      _screenState = NavigationScreenState.readyToLocalize;
      _selectedRoute = null;
      _currentLocation = null;
      _availableRoutes = [];
      _currentInstruction = null;
      _statusMessage = '请站在已录制的节点附近，点击“开始定位”';
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
      await _navigationService.stopNavigation();
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
