import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_sound/flutter_sound.dart' as fs;

import 'bluetooth/bluetooth_spp.dart';
void main() {
  runApp(const RoboguideRemoteApp());
}

class RoboguideRemoteApp extends StatelessWidget {
  const RoboguideRemoteApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Roboguide Remote',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

enum _ConnState { disconnected, connecting, connected, error }

class _HomePageState extends State<HomePage> {
  final _spp = BluetoothSpp();
  fs.FlutterSoundRecorder? _recorder;
  fs.FlutterSoundPlayer? _player;
  StreamSubscription<List<Int16List>>? _recSub;

  List<Map<String, dynamic>> _devices = [];
  String? _selectedMac;
  _ConnState _state = _ConnState.disconnected;
  bool _micActive = false;
  String _log = '';

  static const int _sampleRate = 16000;
  static const int _channels = 1;

  @override
  void initState() {
    super.initState();
    // subscribe to received bytes -> play
    _spp.onData.listen((data) => _playPcm(data));
    _loadDevices();
  }

  Future<void> _loadDevices() async {
    try {
      final devices = await _spp.pairedDevices();
      setState(() => _devices = devices);
      // default to Thor if present
      final thor = devices.where((d) =>
          (d['address'] as String) == 'F8:3D:C6:91:8D:69' ||
          (d['name'] as String).toLowerCase().contains('roboguide') ||
          (d['name'] as String).toLowerCase().contains('localhost'));
      if (thor.isNotEmpty) {
        setState(() => _selectedMac = thor.first['address'] as String);
      }
      _appendLog('found ${devices.length} paired device(s)');
    } catch (e) {
      _appendLog('load devices failed: $e');
    }
  }

  @override
  void dispose() {
    _recSub?.cancel();
    _spp.dispose();
    _recorder?.closeRecorder();
    _player?.closePlayer();
    super.dispose();
  }

  void _appendLog(String line) {
    if (!mounted) return;
    setState(() {
      _log = '${DateTime.now().toString().substring(11, 19)} $line\n$_log'
          .split('\n')
          .take(8)
          .join('\n');
    });
  }

  Future<void> _toggleConnect() async {
    if (_state == _ConnState.connected || _state == _ConnState.connecting) {
      await _spp.disconnect();
      setState(() => _state = _ConnState.disconnected);
      return;
    }
    final mac = _selectedMac;
    if (mac == null) {
      _appendLog('no device selected');
      return;
    }
    setState(() => _state = _ConnState.connecting);
    try {
      final ok = await _spp.connect(mac);
      setState(() => _state = ok ? _ConnState.connected : _ConnState.error);
      _appendLog(ok ? 'connected via Bluetooth SPP: $mac' : 'connect returned false');
    } catch (e) {
      setState(() => _state = _ConnState.error);
      _appendLog('connect failed: $e');
    }
  }

  // ── PTT: capture microphone PCM and send over Bluetooth SPP ─────────
  Future<void> _startTalking() async {
    if (_micActive || _state != _ConnState.connected) return;
    _recorder ??= fs.FlutterSoundRecorder();
    try {
      await _recorder!.openRecorder();
    } catch (e) {
      // Most common cause: RECORD_AUDIO runtime permission not granted.
      _appendLog('openRecorder failed (mic permission?): $e');
      return;
    }
    final sink = StreamController<List<Int16List>>();
    _recSub = sink.stream.listen((chunks) {
      for (final chunk in chunks) {
        _spp.write(Uint8List.view(chunk.buffer));
      }
    });
    try {
      await _recorder!.startRecorder(
        codec: fs.Codec.pcm16,
        toStreamInt16: sink,
        sampleRate: _sampleRate,
        numChannels: _channels,
        enableNoiseSuppression: true,
        enableEchoCancellation: true,
      );
    } catch (e) {
      await _recSub?.cancel();
      _recSub = null;
      _appendLog('startRecorder failed: $e');
      return;
    }
    setState(() => _micActive = true);
  }

  Future<void> _stopTalking() async {
    if (!_micActive) return;
    setState(() => _micActive = false);
    await _recSub?.cancel();
    _recSub = null;
    try {
      await _recorder?.stopRecorder();
    } catch (_) {}
    // signal end-of-utterance
    try {
      await _spp.write(Uint8List.fromList('{"type":"mic_end"}'.codeUnits));
    } catch (_) {}
  }

  Future<void> _playPcm(Uint8List frame) async {
    _player ??= fs.FlutterSoundPlayer();
    try {
      if (!_player!.isPlaying) {
        await _player!.openPlayer();
        await _player!.startPlayerFromStream(
          codec: fs.Codec.pcm16,
          interleaved: true,
          numChannels: _channels,
          sampleRate: _sampleRate,
          bufferSize: 1600,
        );
      }
      await _player!.feedUint8FromStream(frame);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final connected = _state == _ConnState.connected;
    return Scaffold(
      appBar: AppBar(title: const Text('Roboguide Remote')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            DropdownButtonFormField<String>(
              initialValue: _selectedMac,
              items: _devices
                  .map((d) => DropdownMenuItem(
                        value: d['address'] as String,
                        child: Text(
                          '${d['name']} (${d['address']})',
                          overflow: TextOverflow.ellipsis,
                        ),
                      ))
                  .toList(),
              onChanged: connected ? null : (mac) => setState(() => _selectedMac = mac),
              decoration: const InputDecoration(
                labelText: 'Robot (Bluetooth)',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: connected ? null : _loadDevices,
              icon: const Icon(Icons.refresh),
              label: const Text('Refresh devices'),
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: _toggleConnect,
              icon: Icon(connected ? Icons.bluetooth_disabled : Icons.bluetooth),
              label: Text(connected ? 'Disconnect' : 'Connect'),
            ),
            const SizedBox(height: 12),
            _StatusCard(state: _state, micActive: _micActive),
            const SizedBox(height: 16),
            _PttButton(
              enabled: connected,
              talking: _micActive,
              onPressStart: _startTalking,
              onPressEnd: _stopTalking,
            ),
            const SizedBox(height: 16),
            Expanded(
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: Colors.black12,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: SingleChildScrollView(
                  reverse: true,
                  child: Text(
                    _log.isEmpty ? 'log empty' : _log,
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final _ConnState state;
  final bool micActive;
  const _StatusCard({required this.state, required this.micActive});

  @override
  Widget build(BuildContext context) {
    final color = switch (state) {
      _ConnState.connected => Colors.green,
      _ConnState.connecting => Colors.orange,
      _ConnState.error => Colors.red,
      _ConnState.disconnected => Colors.grey,
    };
    final label = switch (state) {
      _ConnState.connected => 'Connected',
      _ConnState.connecting => 'Connecting',
      _ConnState.error => 'Error',
      _ConnState.disconnected => 'Disconnected',
    };
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Icon(Icons.circle, size: 12, color: color),
            const SizedBox(width: 6),
            Text('Bridge: $label',
                style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            if (micActive)
              const Chip(
                label: Text('MIC ACTIVE'),
                backgroundColor: Colors.redAccent,
                labelStyle: TextStyle(color: Colors.white, fontSize: 11),
              ),
          ],
        ),
      ),
    );
  }
}

class _PttButton extends StatefulWidget {
  final bool enabled;
  final bool talking;
  final VoidCallback onPressStart;
  final VoidCallback onPressEnd;
  const _PttButton({
    required this.enabled,
    required this.talking,
    required this.onPressStart,
    required this.onPressEnd,
  });

  @override
  State<_PttButton> createState() => _PttButtonState();
}

class _PttButtonState extends State<_PttButton> {
  bool _pressed = false;

  void _handleDown() {
    if (!widget.enabled) return;
    setState(() => _pressed = true);
    widget.onPressStart();
  }

  void _handleUp() {
    if (!_pressed) return;
    setState(() => _pressed = false);
    widget.onPressEnd();
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.talking || _pressed;
    final color = !widget.enabled
        ? Colors.grey
        : active
            ? Colors.redAccent
            : Colors.teal;
    return Listener(
      onPointerDown: (_) => _handleDown(),
      onPointerUp: (_) => _handleUp(),
      onPointerCancel: (_) => _handleUp(),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 120),
        height: 72,
        decoration: BoxDecoration(
          color: color.withValues(alpha: active ? 0.9 : 0.15),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: color, width: 2),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(active ? Icons.mic : Icons.mic_none, size: 28, color: color),
            const SizedBox(width: 8),
            Text(
              !widget.enabled
                  ? 'Connect first'
                  : active
                      ? 'Speaking...'
                      : 'Hold to Talk',
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
