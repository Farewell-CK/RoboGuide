// 生成一段确定性合成音调（正弦波 + 淡入淡出）作为"伪音频"mock 捕获包，
// 与 capture_controller 的落盘格式完全一致：stream.sppwire + audio.pcm + meta.json。
//
// 用法:
//   dart run tool/gen_tone_capture.dart --out build/tone-capture [--dur-ms 1000] [--freq 440] [--chunk-ms 100] [--session <id>]
// ignore_for_file: avoid_print
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:roboguide_remote/transport/spp_framer.dart';

const int _sampleRate = 16000;

Future<void> main(List<String> args) async {
  final opts = <String, String>{};
  for (var i = 0; i < args.length; i++) {
    if (args[i].startsWith('--') && i + 1 < args.length) {
      opts[args[i].substring(2)] = args[i + 1];
      i++;
    }
  }
  final out = opts['out'];
  if (out == null) {
    stderr.writeln('缺少 --out <目录>');
    exit(64);
  }
  final durMs = int.tryParse(opts['dur-ms'] ?? '') ?? 1000;
  final freq = int.tryParse(opts['freq'] ?? '') ?? 440;
  final chunkMs = int.tryParse(opts['chunk-ms'] ?? '') ?? 100;
  final sessionId = opts['session'] ?? 'tone-${DateTime.now().millisecondsSinceEpoch}';

  final dir = Directory(out)..createSync(recursive: true);
  final tone = _tone(durMs, freq);
  final sampleCount = tone.length ~/ 2;
  final chunkSamples = (chunkMs * _sampleRate / 1000).round();
  final wire = BytesBuilder();
  var chunkCount = 0;
  var wireBytes = 0;
  for (var off = 0; off < sampleCount; off += chunkSamples) {
    final end = min(sampleCount, off + chunkSamples);
    final framed = SppFramer.encodeAudio(tone.sublist(off * 2, end * 2));
    wire.add(framed);
    wireBytes += framed.length;
    chunkCount++;
  }
  final endFrame = SppFramer.encodeControl({'type': 'mic_end', 'stream_id': ''});
  wireBytes += endFrame.length;
  wire.add(endFrame);

  final now = DateTime.now();
  File('${dir.path}/stream.sppwire').writeAsBytesSync(wire.toBytes());
  File('${dir.path}/audio.pcm').writeAsBytesSync(tone);
  File('${dir.path}/meta.json').writeAsStringSync(jsonEncode(<String, dynamic>{
    'version': 1,
    'sessionId': sessionId,
    'mode': 'spp',
    'codec': 'pcm16',
    'sampleRate': _sampleRate,
    'channels': 1,
    'chunkCount': chunkCount,
    'pcmBytes': tone.length,
    'wireBytes': wireBytes,
    'micEnd': true,
    'source': 'tool:gen_tone_capture',
    'startedAt': now.toIso8601String(),
    'endedAt': now.toIso8601String(),
    'toneFreqHz': freq,
    'toneDurMs': durMs,
  }));
  print('tone capture -> ${dir.path}  toneBytes=${tone.length}'
      ' frames=$chunkCount wireBytes=$wireBytes durMs=$durMs freqHz=$freq');
}

/// PCM16/16kHz/mono 正弦波 + 30ms 淡入淡出，确定性（无随机）。
Uint8List _tone(int durMs, int freqHz) {
  final n = (durMs * _sampleRate / 1000).round();
  final fade = (0.030 * _sampleRate).round();
  final out = Int16List(n);
  for (var i = 0; i < n; i++) {
    final env = min(1.0, min(i / fade, (n - 1 - i) / fade).toDouble());
    final v = env * sin(2 * pi * freqHz * i / _sampleRate);
    out[i] = (v * 20000).round();
  }
  return Uint8List.view(out.buffer);
}