import 'dart:async';

import 'package:flutter/services.dart';
/// Thin Flutter wrapper over the native Android Bluetooth SPP plugin.
///
/// MethodChannel 'roboguide/bluetooth_spp' for control,
/// EventChannel  'roboguide/bluetooth_spp/events' for received bytes.
class BluetoothSpp {
  static const MethodChannel _method =
      MethodChannel('roboguide/bluetooth_spp');
  static const EventChannel _events =
      EventChannel('roboguide/bluetooth_spp/events');

  final _dataCtrl = StreamController<Uint8List>.broadcast();
  late final StreamSubscription _sub;

  BluetoothSpp() {
    _sub = _events.receiveBroadcastStream().listen((event) {
      if (event is List<int>) {
        _dataCtrl.add(Uint8List.fromList(event));
      }
    }, onDone: () => _dataCtrl.addError('connection closed'));
  }

  Stream<Uint8List> get onData => _dataCtrl.stream;

  /// Return the list of bonded (paired) Bluetooth devices.
  Future<List<Map<String, dynamic>>> pairedDevices() async {
    final result = await _method.invokeMethod('getBondedDevices');
    if (result is List) {
      return result.cast<Map<dynamic, dynamic>>().map((m) {
        return {
          'name': m['name'] as String? ?? '',
          'address': m['address'] as String? ?? '',
        };
      }).toList();
    }
    return [];
  }

  Future<bool> connect(String mac) async {
    final ok = await _method.invokeMethod<bool>('connect', {'mac': mac});
    return ok ?? false;
  }

  Future<void> disconnect() async {
    await _method.invokeMethod('disconnect');
  }

  Future<bool> get isConnected async {
    final ok = await _method.invokeMethod<bool>('isConnected');
    return ok ?? false;
  }

  Future<void> write(Uint8List data) async {
    await _method.invokeMethod('write', {'bytes': data});
  }

  void dispose() {
    _sub.cancel();
    _dataCtrl.close();
  }
}
