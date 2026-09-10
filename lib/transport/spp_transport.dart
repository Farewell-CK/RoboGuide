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
  StreamSubscription? _linkCtrlSub;
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
        TransportStatus(
          TransportStatusKind.disconnected,
          reason: 'connect returned false',
        ),
      );
      throw StateError('spp connect failed');
    }
    _connected = true;
    _statusCtrl.add(const TransportStatus(TransportStatusKind.connected));
    _linkSub?.cancel();
    _linkSub = _link.onData.listen(
      _onBytes,
      onDone: _onLinkClosed,
      onError: (_) => _onLinkClosed(),
    );
    // BluetoothSpp already split RGAD audio / RGCT control. Control frames
    // (voice_event etc.) live on onControl — without this subscription all
    // server responses (ASR text / pilot replies / errors) were dropped and
    // RX stayed 0 while the raw audio path re-framed bare PCM pointlessly.
    _linkCtrlSub?.cancel();
    _linkCtrlSub = _link.onControl.listen((e) => _controlCtrl.add(e.value));
  }

  void _onBytes(Uint8List bytes) {
    // audio payload is already un-framed by BluetoothSpp
    _audioCtrl.add(bytes);
  }

  void _onLinkClosed() {
    if (!_connected) return;
    _connected = false;
    _statusCtrl.add(
      const TransportStatus(
        TransportStatusKind.disconnected,
        reason: 'link closed',
      ),
    );
  }

  @override
  Future<void> disconnect() async {
    _connected = false;
    await _linkSub?.cancel();
    _linkSub = null;
    await _linkCtrlSub?.cancel();
    _linkCtrlSub = null;
    try {
      await _link.disconnect();
    } catch (_) {}
    _statusCtrl.add(
      const TransportStatus(TransportStatusKind.disconnected, reason: 'user'),
    );
  }

  @override
  Future<void> sendAudio(Uint8List pcm) => _link.writeAudio(pcm);

  @override
  Future<void> sendControl(Map<String, dynamic> message) =>
      _link.writeControl(message);

  @override
  void dispose() {
    _linkSub?.cancel();
    _linkCtrlSub?.cancel();
    _link.dispose();
    _statusCtrl.close();
    _audioCtrl.close();
    _controlCtrl.close();
  }
}
