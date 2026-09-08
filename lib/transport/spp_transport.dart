import 'dart:async';
import 'dart:typed_data';

import '../bluetooth/bluetooth_spp.dart';
import 'robot_transport.dart';
import 'spp_framer.dart';

/// Bluetooth classic SPP transport: reassembles RGAD/RGCT frames from the
/// raw native byte stream and exposes bare-PCM audio + JSON control, matching
/// the [RobotTransport] contract.
class SppTransport implements RobotTransport {
  final BluetoothSpp _link = BluetoothSpp();
  final SppFramer _framer = SppFramer();
  StreamSubscription? _linkSub;
  bool _connected = false;

  final _statusCtrl = StreamController<TransportStatus>.broadcast();
  final _audioCtrl = StreamController<Uint8List>.broadcast();
  final _controlCtrl = StreamController<Map<String, dynamic>>.broadcast();

  SppTransport();

  String? lastMac;

  @override
  TransportMode get mode => TransportMode.spp;

  @override
  Stream<TransportStatus> get status => _statusCtrl.stream;

  @override
  Stream<Uint8List> get audio => _audioCtrl.stream;

  @override
  Stream<Map<String, dynamic>> get control => _controlCtrl.stream;

  @override
  bool get connected => _connected;

  Future<List<Map<String, dynamic>>> pairedDevices() => _link.pairedDevices();

  @override
  Future<void> connect() async {
    final mac = lastMac;
    if (mac == null || mac.isEmpty) {
      throw StateError('spp: no mac configured');
    }
    _statusCtrl.add(const TransportStatus(TransportStatusKind.connecting));
    final ok = await _link.connect(mac);
    if (!ok) {
      _statusCtrl.add(
        TransportStatus(TransportStatusKind.disconnected, reason: 'connect returned false'),
      );
      throw StateError('spp connect failed');
    }
    _connected = true;
    _statusCtrl.add(const TransportStatus(TransportStatusKind.connected));
    _linkSub?.cancel();
    _linkSub = _link.onData.listen(_onBytes, onDone: _onLinkClosed, onError: (_) => _onLinkClosed());
  }

  void _onBytes(Uint8List bytes) {
    _framer.add(bytes);
    for (final frame in _framer.takeFrames()) {
      if (frame.type == SppFrameType.audio) {
        _audioCtrl.add(frame.payload);
      } else {
        try {
          final value = jsonDecodeUtf8(frame.payload);
          _controlCtrl.add(value);
        } catch (_) {
          // ignore malformed control frames
        }
      }
    }
  }

  void _onLinkClosed() {
    if (!_connected) return;
    _connected = false;
    _statusCtrl.add(
      const TransportStatus(TransportStatusKind.disconnected, reason: 'link closed'),
    );
  }

  @override
  Future<void> disconnect() async {
    _connected = false;
    await _linkSub?.cancel();
    _linkSub = null;
    try {
      await _link.disconnect();
    } catch (_) {}
    _statusCtrl.add(const TransportStatus(TransportStatusKind.disconnected, reason: 'user'));
  }

  @override
  Future<void> sendAudio(Uint8List pcm) => _link.writeAudio(pcm);

  @override
  Future<void> sendControl(Map<String, dynamic> message) => _link.writeControl(message);

  @override
  void dispose() {
    _linkSub?.cancel();
    _link.dispose();
    _statusCtrl.close();
    _audioCtrl.close();
    _controlCtrl.close();
  }
}
