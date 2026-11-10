import 'dart:async';
import 'dart:io';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_compass/flutter_compass.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:permission_handler/permission_handler.dart';
import '../../services/supabase_service.dart';
import '../../services/dinov2_service.dart';
import '../../services/continuous_path_recorder.dart';
import '../../models/path_models.dart';
import 'package:uuid/uuid.dart';

class PathRecordingScreen extends StatefulWidget {
  final CameraDescription camera;
  final String startLocationId;
  final String endLocationId;
  final String startLocationName;
  final String endLocationName;

  const PathRecordingScreen({
    Key? key,
    required this.camera,
    required this.startLocationId,
    required this.endLocationId,
    required this.startLocationName,
    required this.endLocationName,
  }) : super(key: key);

  @override
  _PathRecordingScreenState createState() => _PathRecordingScreenState();
}

class _PathRecordingScreenState extends State<PathRecordingScreen> {
  late CameraController _cameraController;
  late ClipService _clipService;
  late ContinuousPathRecorder _pathRecorder;
  late FlutterTts _flutterTts;
  
  bool _isLoading = true;
  bool _isClipServerReady = false;
  String _statusMessage = "正在初始化…";
  int _waypointCount = 0;
  bool _isRecording = false;
  bool _canRetryProcessing = false;
  bool _isSaving = false;
  bool _isProcessing = false; // Track when waypoints are being processed
  
  // Overlay message for center screen display
  String _overlayMessage = "";
  Timer? _overlayTimer;
  
  // Path info
  final TextEditingController _pathNameController = TextEditingController();
  
  // Current heading for display
  double? _currentHeading;
  StreamSubscription<CompassEvent>? _compassSubscription;

  @override
  void initState() {
    super.initState();
    _initializeComponents();
  }

  Future<void> _initializeComponents() async {
    await _requestPermissions();
    await _initializeCamera();
    await _initializeClipService();
    await _initializeTts();
    await _initializePathRecorder();
    _initializeCompass();
    
    setState(() {
      _isLoading = false;
      _statusMessage = "";
    });
  }

  Future<void> _requestPermissions() async {
    if (Platform.isAndroid) {
      await [
        Permission.camera,
        Permission.location,
        Permission.microphone,
      ].request();
    }
  }

  Future<void> _initializeCamera() async {
    _cameraController = CameraController(
      widget.camera,
      ResolutionPreset.medium,
      enableAudio: false,
      imageFormatGroup: ImageFormatGroup.yuv420,
    );

    try {
      await _cameraController.initialize();
      await _cameraController.setFlashMode(FlashMode.off);
    } catch (e) {
      setState(() {
        _statusMessage = "Camera error: $e";
      });
    }
  }

  Future<void> _initializeClipService() async {
    try {
      _clipService = ClipService();
      _isClipServerReady = await _clipService.isServerAvailable();
    } catch (e) {
      setState(() {
        _statusMessage = "CLIP service error: $e";
      });
    }
  }

  Future<void> _initializeTts() async {
    _flutterTts = FlutterTts();
    await _flutterTts.setLanguage("en-US");
    await _flutterTts.setSpeechRate(0.5);
    await _flutterTts.setVolume(1.0);
    await _flutterTts.setPitch(1.0);
  }

  Future<void> _initializePathRecorder() async {
    _pathRecorder = ContinuousPathRecorder(
      clipService: _clipService,
      cameraController: _cameraController,
      onStatusUpdate: (message) {
        // Only handle non-waypoint and non-recording messages with status display
        if ((!message.contains('Waypoint') || !message.contains('captured')) && 
            !message.contains('录制已开始')) {
          setState(() {
            _statusMessage = message;
          });
        }
        // Audio feedback for waypoint captures and recording started
        if ((message.contains('Waypoint') && message.contains('captured')) || 
            message.contains('录制已开始')) {
          _speak(message);
          // Show overlay message in center of screen instead of snackbar
          _showOverlayMessage(message, durationSeconds: 2);
        }
      },
      onWaypointCaptured: (waypoint) {
        setState(() {
          _waypointCount = _pathRecorder.waypointCount;
        });
      },
      onError: (error) {
        setState(() {
          _statusMessage = "Error: $error";
        });
        _speak("Recording error occurred");
      },
    );
  }

  void _initializeCompass() {
    if (FlutterCompass.events != null) {
      _compassSubscription = FlutterCompass.events!.listen((event) {
        if (mounted && event.heading != null) {
          setState(() {
            _currentHeading = event.heading;
          });
        }
      });
    }
  }

  Future<void> _speak(String text) async {
    if (text.isNotEmpty) {
      await _flutterTts.speak(text);
    }
  }

  void _showOverlayMessage(String message, {int durationSeconds = 2}) {
    setState(() {
      _overlayMessage = message;
    });
    
    // Cancel any existing timer
    _overlayTimer?.cancel();
    
    // Set timer to hide the overlay
    _overlayTimer = Timer(Duration(seconds: durationSeconds), () {
      if (mounted) {
        setState(() {
          _overlayMessage = "";
        });
      }
    });
  }

  Future<void> _startRecording() async {
    if (!_isClipServerReady) {
      _speak("CLIP service not ready. Cannot start recording.");
      return;
    }

    const uuid = Uuid();
    String pathId = uuid.v4();
    
    await _pathRecorder.startRecording(pathId);
    setState(() {
      _isRecording = true;
      _canRetryProcessing = false;
      _waypointCount = 0;
      _isProcessing = false; // Reset processing state when starting new recording
    });
  }

  Future<void> _stopRecording() async {
    await _processRecording();
  }

  Future<void> _processRecording({bool retry = false}) async {
    setState(() {
      _isRecording = false;
      _isProcessing = true;
      _statusMessage = '正在处理图片，请保持网络连接…';
    });
    try {
      if (retry) {
        await _pathRecorder.processCapturedFrames();
      } else {
        await _pathRecorder.stopRecording();
      }
      if (!mounted) return;
      setState(() { _isProcessing = false; _canRetryProcessing = false; _waypointCount = _pathRecorder.waypointCount; _statusMessage = ''; });
      if (_pathRecorder.waypointCount >= 2) {
        _showSavePathDialog();
      } else {
        setState(() { _statusMessage = '采样点不足，请重新录制一段路线。'; });
      }
    } catch (error) {
      if (!mounted) return;
      setState(() { _isProcessing = false; _canRetryProcessing = true; _waypointCount = 0; _statusMessage = error.toString(); });
      final retryNow = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('路线尚未保存'),
          content: Text('$error\n\n本次图片仍保留在此页面，恢复连接后可重试处理。离开此页面后需要重新录制。'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('稍后再试')),
            TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('重试处理')),
          ],
        ),
      );
      if (retryNow == true && mounted) await _processRecording(retry: true);
    }
  }

  void _showSavePathDialog() {
    // Pre-fill path name
        // Initialize default path name with node names
    _pathNameController.text = '${widget.startLocationName} to ${widget.endLocationName}';
    
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('保存录制的路线'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: _pathNameController,
                decoration: const InputDecoration(
                  labelText: '路线名称',
                  hintText: '例如：办公室门口到电梯口',
                ),
              ),
              const SizedBox(height: 16),
              Text(
                '已录制 ${_pathRecorder.waypointCount} 个采样点',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () {
              // Cancel any ongoing waypoint processing
              _pathRecorder.cancelProcessing();
              Navigator.pop(context);
              // Optionally ask if they want to discard the recording
            },
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () async {
              final path = await _savePath();
              if (path != null && context.mounted && mounted) {
                Navigator.pop(context);
                Navigator.pop(this.context, path);
              }
            },
            child: const Text('保存路线'),
          ),
        ],
      ),
    );
  }

  Future<NavigationPath?> _savePath() async {
    if (_isSaving) return null;
    if (_pathNameController.text.isEmpty) {
      _speak("Please enter a path name");
      return null;
    }

    _isSaving = true;
    try {
      NavigationPath path = _pathRecorder.createNavigationPath(
        name: _pathNameController.text,
        startLocationId: widget.startLocationId,
        endLocationId: widget.endLocationId,
      );

      // Save to Supabase with enhanced error handling
      final supabaseService = SupabaseService();

      try {
        await supabaseService.savePath(path);
      } catch (saveError) {
        print(' Save path error: $saveError');

        // Check if it's an RLS policy violation
        if (saveError.toString().contains('violates row-level security policy') ||
            saveError.toString().contains('Authentication error')) {

          // Try to refresh the session
          final sessionRefreshed = await supabaseService.refreshSession();

          if (sessionRefreshed) {
            // Try saving again after session refresh
            try {
              await supabaseService.savePath(path);
            } catch (retryError) {
              rethrow;
            }
          } else {
            setState(() {
              _statusMessage = "登录会话刷新失败，请退出后重新登录。";
            });
            _speak("Authentication failed. Please sign out and sign in again.");
            throw Exception('登录已过期，请重新登录后再保存。');
          }
        } else {
          // Re-throw other errors
          rethrow;
        }
      }

      return path;

    } catch (e) {
      _speak("Error saving path");
      if (mounted) setState(() { _statusMessage = '路线未保存：$e'; });
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('路线未保存：$e')));
      return null;
    } finally {
      _isSaving = false;
    }
  }

  String _getDirectionName(double? heading) {
    if (heading == null) return "Unknown";
    
    int normalizedAngle = heading.round() % 360;
    if (normalizedAngle >= 348.75 || normalizedAngle < 11.25) return "北";
    if (normalizedAngle >= 11.25 && normalizedAngle < 33.75) return "North-Northeast";
    if (normalizedAngle >= 33.75 && normalizedAngle < 56.25) return "东北";
    if (normalizedAngle >= 56.25 && normalizedAngle < 78.75) return "East-Northeast";
    if (normalizedAngle >= 78.75 && normalizedAngle < 101.25) return "东";
    if (normalizedAngle >= 101.25 && normalizedAngle < 123.75) return "East-Southeast";
    if (normalizedAngle >= 123.75 && normalizedAngle < 146.25) return "东南";
    if (normalizedAngle >= 146.25 && normalizedAngle < 168.75) return "South-Southeast";
    if (normalizedAngle >= 168.75 && normalizedAngle < 191.25) return "南";
    if (normalizedAngle >= 191.25 && normalizedAngle < 213.75) return "South-Southwest";
    if (normalizedAngle >= 213.75 && normalizedAngle < 236.25) return "西南";
    if (normalizedAngle >= 236.25 && normalizedAngle < 258.75) return "West-Southwest";
    if (normalizedAngle >= 258.75 && normalizedAngle < 281.25) return "西";
    if (normalizedAngle >= 281.25 && normalizedAngle < 303.75) return "West-Northwest";
    if (normalizedAngle >= 303.75 && normalizedAngle < 326.25) return "西北";
    if (normalizedAngle >= 326.25 && normalizedAngle < 348.75) return "North-Northwest";
    return "$normalizedAngle°";
  }

  Widget _buildCameraPreview(double screenWidth, double screenHeight) {
    return SizedBox.expand(
      child: Stack(
        children: [
          // Camera preview (same as navigation screen)
          CameraPreview(_cameraController),

          // Status overlay - responsive positioning
          Positioned(
            top: screenHeight * 0.02,
            left: screenWidth * 0.05,
            child: Container(
              padding: EdgeInsets.all(screenWidth * 0.02),
              decoration: BoxDecoration(
                color: Colors.black54,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    "采样点：$_waypointCount",
                    style: TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      fontSize: screenWidth * 0.03,
                    ),
                  ),
                  Text(
                    "朝向：${_getDirectionName(_currentHeading)}",
                    style: TextStyle(
                      color: Colors.white70,
                      fontSize: screenWidth * 0.025,
                    ),
                  ),
                  if (_currentHeading != null)
                    Text(
                      "${_currentHeading!.round()}°",
                      style: TextStyle(
                        color: Colors.white70,
                        fontSize: screenWidth * 0.025,
                      ),
                    ),
                ],
              ),
            ),
          ),

          // Status message overlay - responsive positioning
          if (_statusMessage.isNotEmpty && !_isProcessing)
            Positioned(
              left: screenWidth * 0.05,
              right: screenWidth * 0.05,
              bottom: screenHeight * 0.02,
              child: Container(
                padding: EdgeInsets.all(screenWidth * 0.02),
                decoration: BoxDecoration(
                  color: Colors.black54,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  _statusMessage,
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: screenWidth * 0.03,
                  ),
                  textAlign: TextAlign.center,
                ),
              ),
            ),

          // Processing overlay - blocks interactions during waypoint processing
          if (_isProcessing)
            Positioned.fill(
              child: Container(
                color: Colors.black.withValues(alpha: 0.8),
                child: Center(
                  child: Container(
                    padding: EdgeInsets.all(screenWidth * 0.06),
                    margin: EdgeInsets.symmetric(horizontal: screenWidth * 0.1),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(20),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.3),
                          blurRadius: 10,
                          spreadRadius: 2,
                        ),
                      ],
                    ),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        // Processing indicator
                        SizedBox(
                          width: screenWidth * 0.15,
                          height: screenWidth * 0.15,
                          child: const CircularProgressIndicator(
                            valueColor: AlwaysStoppedAnimation<Color>(Colors.teal),
                            strokeWidth: 4,
                          ),
                        ),
                        SizedBox(height: screenHeight * 0.02),
                        // Processing title
                        Text(
                          "正在处理路线特征",
                          style: TextStyle(
                            fontSize: screenWidth * 0.045,
                            fontWeight: FontWeight.bold,
                            color: Colors.black87,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        SizedBox(height: screenHeight * 0.01),
                        // Processing message
                        Text(
                          _statusMessage,
                          style: TextStyle(
                            fontSize: screenWidth * 0.035,
                            color: Colors.black54,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        SizedBox(height: screenHeight * 0.02),
                        // Processing hint
                        Text(
                          "正在处理录制的路线，请稍候…",
                          style: TextStyle(
                            fontSize: screenWidth * 0.03,
                            color: Colors.black45,
                            fontStyle: FontStyle.italic,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),

          // Center overlay for recording status messages
          if (_overlayMessage.isNotEmpty)
            Positioned.fill(
              child: Center(
                child: Container(
                  padding: EdgeInsets.symmetric(
                    horizontal: screenWidth * 0.08,
                    vertical: screenWidth * 0.06,
                  ),
                  margin: EdgeInsets.symmetric(horizontal: screenWidth * 0.1),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.4),
                    borderRadius: BorderRadius.circular(16),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withValues(alpha: 0.5),
                        blurRadius: 10,
                        spreadRadius: 2,
                      ),
                    ],
                  ),
                  child: Text(
                    _overlayMessage,
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: screenWidth * 0.05,
                      fontWeight: FontWeight.w600,
                      shadows: [
                        Shadow(
                          blurRadius: 3.0,
                          color: Colors.black,
                          offset: Offset(1.0, 1.0),
                        ),
                      ],
                    ),
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildBottomControls(double screenWidth, double screenHeight) {
    return Container(
      padding: EdgeInsets.all(screenWidth * 0.04),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Instructions section - uses status messages from continuous_path_recorder
          if (_canRetryProcessing)
            ElevatedButton.icon(
              onPressed: _isProcessing ? null : () => _processRecording(retry: true),
              icon: const Icon(Icons.refresh),
              label: const Text('恢复连接后重试处理'),
            ),
          if (_isRecording || _waypointCount > 0)
            Padding(
              padding: EdgeInsets.symmetric(
                horizontal: screenWidth * 0.04,
                vertical: screenHeight * 0.01,
              ),
              child: Column(
                children: [
                  // Main instruction from status message
                  if (_statusMessage.isNotEmpty && !_isProcessing)
                    Text(
                      _statusMessage,
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: screenWidth * 0.040,
                        fontWeight: FontWeight.w500,
                        shadows: [
                          Shadow(
                            blurRadius: 2.0,
                            color: Colors.black,
                            offset: Offset(1.0, 1.0),
                          ),
                        ],
                      ),
                      textAlign: TextAlign.center,
                    ),
                  // Waypoint count
                  if (_waypointCount > 0)
                    Padding(
                      padding: EdgeInsets.only(top: screenHeight * 0.005),
                      child: Text(
                        "已采集第 ${_waypointCount} 个采样点",
                        style: TextStyle(
                          color: Colors.green[300],
                          fontSize: screenWidth * 0.03,
                          fontWeight: FontWeight.w400,
                          shadows: [
                            Shadow(
                              blurRadius: 2.0,
                              color: Colors.black,
                              offset: Offset(1.0, 1.0),
                            ),
                          ],
                        ),
                        textAlign: TextAlign.center,
                      ),
                    ),
                ],
              ),
            ),

          // Route info - always shown above buttons
          Container(
            padding: EdgeInsets.symmetric(
              horizontal: screenWidth * 0.04,
              vertical: screenHeight * 0.015,
            ),
            margin: EdgeInsets.only(bottom: screenHeight * 0.02),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.7),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Flexible(
                  child: Text(
                    widget.startLocationName,
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: screenWidth * 0.05,
                      fontWeight: FontWeight.w500,
                    ),
                    textAlign: TextAlign.center,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                SizedBox(width: screenWidth * 0.02),
                Icon(
                  Icons.arrow_forward,
                  color: Colors.teal,
                  size: screenWidth * 0.05,
                ),
                SizedBox(width: screenWidth * 0.02),
                Flexible(
                  child: Text(
                    widget.endLocationName,
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: screenWidth * 0.05,
                      fontWeight: FontWeight.w500,
                    ),
                    textAlign: TextAlign.center,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),

          // Main control buttons - equal size
          if (!_isRecording)
            Row(
              children: [
                // Cancel button - equal size, white text and icon
                Expanded(
                  child: OutlinedButton.icon(
                    icon: Icon(Icons.cancel, size: screenWidth * 0.06),
                    label: Text(
                      "取消",
                      style: TextStyle(fontSize: screenWidth * 0.04),
                    ),
                    onPressed: _isProcessing ? null : () => Navigator.of(context).pop(),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white,
                      side: BorderSide(color: Colors.white),
                      padding: EdgeInsets.symmetric(vertical: screenHeight * 0.02),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                  ),
                ),
                SizedBox(width: screenWidth * 0.02),
                // Start Recording button - equal size (removed flex: 2)
                Expanded(
                  child: ElevatedButton.icon(
                    icon: Icon(Icons.play_arrow, size: screenWidth * 0.06),
                    label: Text(
                      "开始录制",
                      style: TextStyle(fontSize: screenWidth * 0.04),
                    ),
                    onPressed: _isProcessing ? null : (_isClipServerReady ? _startRecording : null),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: _isProcessing ? Colors.grey[400] : Colors.green,
                      foregroundColor: Colors.white,
                      padding: EdgeInsets.symmetric(vertical: screenHeight * 0.02),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                  ),
                ),
              ],
            ),

          if (_isRecording)
            // Stop Recording button - full width
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                icon: Icon(Icons.stop, size: screenWidth * 0.06),
                label: Text(
                  "停止录制",
                  style: TextStyle(fontSize: screenWidth * 0.04),
                ),
                onPressed: _isProcessing ? null : _stopRecording,
                style: ElevatedButton.styleFrom(
                  backgroundColor: _isProcessing ? Colors.grey[400] : Colors.red,
                  foregroundColor: Colors.white,
                  padding: EdgeInsets.symmetric(vertical: screenHeight * 0.02),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),

          if (!_isRecording && _waypointCount > 0)
            Padding(
              padding: EdgeInsets.only(top: screenHeight * 0.015),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  icon: Icon(Icons.save, size: screenWidth * 0.045),
                  label: Text(
                    "保存路线",
                    style: TextStyle(fontSize: screenWidth * 0.03),
                  ),
                  onPressed: _isProcessing ? null : () => _showSavePathDialog(),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: _isProcessing ? Colors.grey[400] : Colors.teal,
                    foregroundColor: Colors.white,
                    padding: EdgeInsets.symmetric(vertical: screenHeight * 0.015),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _overlayTimer?.cancel();
    _compassSubscription?.cancel();
    _pathRecorder.dispose();
    _cameraController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final screenSize = MediaQuery.of(context).size;
    final screenWidth = screenSize.width;
    final screenHeight = screenSize.height;

    if (_isLoading) {
      return Scaffold(
        body: Container(
          color: Colors.teal,
          child: const SafeArea(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircularProgressIndicator(color: Colors.white),
                  SizedBox(height: 20),
                  Text(
                    "正在初始化路线录制…",
                    style: TextStyle(color: Colors.white, fontSize: 16),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    if (!_cameraController.value.isInitialized) {
      return Scaffold(
        body: Container(
          color: Colors.teal,
          child: const SafeArea(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  CircularProgressIndicator(color: Colors.white),
                  SizedBox(height: 20),
                  Text(
                    "正在初始化相机…",
                    style: TextStyle(color: Colors.white, fontSize: 16),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    return Scaffold(
      body: Container(
        color: Colors.black,
        child: SafeArea(
          child: Stack(
            children: [
              // Full screen camera preview (same as navigation screen)
              _buildCameraPreview(screenWidth, screenHeight),

              // Bottom controls overlay
              Positioned(
                left: 0,
                right: 0,
                bottom: 0,
                child: _buildBottomControls(screenWidth, screenHeight),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
