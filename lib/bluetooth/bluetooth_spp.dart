import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/services.dart';

import 'spp_protocol.dart';

/// A decoded control event received from Thor.
class SppControlEvent {
  final Map<String, dynamic> value;
  const SppControlEvent(this.value);
}

/// Thin Flutter wrapper over the native Android Bluetooth SPP plugin.
/// The native channel can deliver arbitrary Bluetooth chunks; this class
/// reassembles RGAD/RGCT frames before exposing audio and control streams.
class BluetoothSpp {
  static const MethodChannel _method = MethodChannel('roboguide/bluetooth_spp');
  static const EventChannel _events = EventChannel('roboguide/bluetooth_spp/events');

  final _dataCtrl = StreamController<Uint8List>.broadcast();
  final _controlCtrl = StreamController<SppControlEvent>.broadcast();
  final _rxFramer = SppFramer();
  late final StreamSubscription _sub;

  BluetoothSpp() {
    _sub = _events.receiveBroadcastStream().listen((event) {
      if (event is Uint8List) {
        _consume(event);
      } else if (event is List) {
        _consume(Uint8List.fromList(event.cast<int>()));
      }
    }, onDone: () => _dataCtrl.addError('connection closed'));
  }

  void _consume(Uint8List bytes) {
    _rxFramer.add(bytes);
    for (final frame in _rxFramer.takeFrames()) {
      if (frame.type == SppFrameType.audio) {
        _dataCtrl.add(frame.payload);
      } else {
        try {
          final value = jsonDecodeUtf8(frame.payload);
          if (value is Map<String, dynamic>) {
            _controlCtrl.add(SppControlEvent(value));
          }
        } catch (_) {
          // Ignore malformed control events; the audio stream remains alive.
        }
      }
    }
  }

  Stream<Uint8List> get onData => _dataCtrl.stream;
  Stream<SppControlEvent> get onControl => _controlCtrl.stream;

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

  /// Send a framed PCM payload to Thor.
  Future<void> writeAudio(Uint8List data) async {
    await _method.invokeMethod('write', {'bytes': SppFramer.encodeAudio(data)});
  }

  /// Send a framed JSON control message to Thor.
  Future<void> writeControl(Map<String, dynamic> value) async {
    await _method.invokeMethod('write', {'bytes': SppFramer.encodeControl(value)});
  }

  void dispose() {
    _sub.cancel();
    _dataCtrl.close();
    _controlCtrl.close();
  }
}

Map<String, dynamic> jsonDecodeUtf8(Uint8List bytes) {
  // UTF-8 decode, NOT String.fromCharCodes (which maps each byte to a code
  // unit and mangles multi-byte CJK into mojibake like '½½□').
  final value = jsonDecode(utf8.decode(bytes));
  if (value is! Map) throw const FormatException('control payload is not an object');
  return value.cast<String, dynamic>();
}
