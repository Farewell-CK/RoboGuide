import 'dart:async';
import 'dart:math';

import '../config/app_config.dart';
import '../transport/robot_transport.dart';
import '../transport/spp_transport.dart';
import '../transport/ws_transport.dart';

/// Owns the active [RobotTransport], switches backends per settings, and
/// keeps the link alive with bounded exponential-backoff auto-reconnect.
class ConnectionManager {
  final AppSettings settings;
  final _statusCtrl = StreamController<TransportStatus>.broadcast();
  final _logCtrl = StreamController<String>.broadcast();

  RobotTransport? _transport;
  StreamSubscription? _statusSub;
  Timer? _retryTimer;
  int _attempt = 0;
  bool _wantConnected = false;
  bool _disposed = false;

  int reconnectCount = 0;
  DateTime? _connectedAt;

  ConnectionManager(this.settings);

  Stream<TransportStatus> get status => _statusCtrl.stream;
  Stream<String> get logs => _logCtrl.stream;
  bool get connected => _transport?.connected ?? false;
  RobotTransport? get transport => _transport;

  Future<void> start() async {
    _wantConnected = true;
    await _open();
  }

  Future<void> _open() async {
    await _closeTransport();
    final t = _buildTransport();
    _transport = t;
    _statusSub?.cancel();
    _statusSub = t.status.listen(_onTransportStatus);
    _logCtrl.add('connecting via ${t.mode.name}...');
    try {
      await t.connect();
    } catch (e) {
      _logCtrl.add('connect failed: $e');
      _scheduleRetry();
    }
  }

  RobotTransport _buildTransport() {
    if (settings.mode == TransportMode.ws) {
      return WsTransport(host: settings.wsHost, port: settings.wsPort);
    }
    final spp = SppTransport();
    spp.lastMac = settings.mac;
    return spp;
  }

  void _onTransportStatus(TransportStatus s) {
    switch (s.kind) {
      case TransportStatusKind.connected:
        _retryTimer?.cancel();
        _retryTimer = null;
        _attempt = 0;
        _connectedAt = DateTime.now();
        _logCtrl.add('connected (${_transport?.mode.name})');
      case TransportStatusKind.disconnected:
        _connectedAt = null;
        _logCtrl.add('disconnected: ${s.reason}');
        if (_wantConnected && settings.autoReconnect && s.reason != 'user') {
          _scheduleRetry();
        }
      case TransportStatusKind.connecting:
        break;
    }
    _statusCtrl.add(s);
  }

  void _scheduleRetry() {
    if (_disposed || _retryTimer != null) return;
    _attempt++;
    final delay = Duration(milliseconds: min(30000, 1000 * pow(2, min(_attempt, 5)).toInt()));
    _logCtrl.add('reconnect in ${delay.inMilliseconds}ms (attempt $_attempt)');
    _retryTimer = Timer(delay, () async {
      _retryTimer = null;
      if (!_wantConnected || _disposed) return;
      reconnectCount++;
      await _open();
    });
  }

  /// Stop the link (user intent). Cancels pending retries.
  Future<void> stop() async {
    _wantConnected = false;
    _retryTimer?.cancel();
    _retryTimer = null;
    await _closeTransport();
    _statusCtrl.add(const TransportStatus(TransportStatusKind.disconnected, reason: 'user'));
  }

  /// Switch backend (mode/host/mac changed) and reconnect.
  Future<void> reconfigure() async {
    await stop();
    await start();
  }

  Future<void> _closeTransport() async {
    _statusSub?.cancel();
    _statusSub = null;
    final t = _transport;
    _transport = null;
    if (t != null) {
      try {
        await t.disconnect();
      } catch (_) {}
      t.dispose();
    }
  }

  /// Uptime of the current link, null when not connected.
  Duration? get uptime => _connectedAt == null ? null : DateTime.now().difference(_connectedAt!);

  void dispose() {
    _disposed = true;
    _retryTimer?.cancel();
    _closeTransport();
    _statusCtrl.close();
    _logCtrl.close();
  }
}
