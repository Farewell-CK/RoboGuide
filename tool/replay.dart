// RoboGuide 离线捕获回放 CLI。
//
// 读取客户端"SPP 传输前"捕获包（capture_controller 生成的 .sppwire 等），把
// 与真实客户端完全一致的字节流送给服务端 mock，从而在无法建立 SPP/WS 连接的
// 时候也能离线驱动"收到音频 → 处理 → 准备回传"整条链路。
//
// 服务端实际拓扑（已核对 100.72.167.58）：
//   thor_spp_server.py  = BlueZ RFCOMM SPP(channel 1)，把手机帧桥接到内部
//                        ws://127.0.0.1:60002/client + Liaison gRPC :50081；
//   回传给手机的 = RGCT voice_event + RGAD TTS 音频。
//   ws 模式连的正是那个 :60002 桥，与官方 Android AudioBridge / 本仓库
//   WsTransport 同协议；加上 --grpc 会话驱动就等同完整客户端。
//
// 用法:
//   dart run tool/replay.dart meta --dir <捕获目录>
//       打印 meta.json + 帧清单，快速核对捕获内容。
//   dart run tool/replay.dart spp  --dir <捕获目录> --host <mock ip> --port <tcp> [--pace 毫秒] [--wait 毫秒]
//       按 RGAD/RGCT 原样字节，经 TCP 转发给本地 mock SPP 服务端。
//   dart run tool/replay.dart ws   --dir <捕获目录> --host <服务端> [--port 60002] [--liaison-port 50081] [--pace 毫秒] [--wait 毫秒] [--no-grpc] [--session <id>]
//       连 ws://host:port/client 发裸 PCM + mic_end，并通过 Liaison gRPC
//       StartVoiceSession/FinishVoiceCapture 驱动真正的 ASR/Pilot 语音会话；
//       打印服务端"准备回传"的 voice_event 与 TTS 音频字节数。
//
// ⚠ 注意：对真实机器人 --host 100.72.167.58 回放会驱动真实 ASR/Pilot/TTS，
//   若捕获音频含运动指令，机器人可能真的动作——测试请先确认捕获内容安全。
// ignore_for_file: avoid_print
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:roboguide_remote/grpc/liaison_client.dart';
import 'package:roboguide_remote/grpc/liaison_codec.dart';
import 'package:roboguide_remote/session/pilot_text.dart';
import 'package:roboguide_remote/transport/spp_framer.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

Future<void> main(List<String> args) async {
  final cmd = args.isEmpty ? 'help' : args[0];
  final opts = _parseOpts(args.sublist(1));
  switch (cmd) {
    case 'meta':
      return _cmdMeta(opts);
    case 'spp':
      return _cmdSpp(opts);
    case 'ws':
      return _cmdWs(opts);
    case 'task':
      return _cmdTask(opts);
    default:
      return _usage();
  }
}

Map<String, String> _parseOpts(List<String> toks) {
  final map = <String, String>{};
  for (var i = 0; i < toks.length; i++) {
    final t = toks[i];
    if (!t.startsWith('--')) continue;
    final key = t.substring(2);
    final hasValue =
        i + 1 < toks.length && !toks[i + 1].startsWith('--');
    map[key] = hasValue ? toks[i + 1] : '';
    if (hasValue) i++;
  }
  return map;
}

void _usage() {
  stderr.writeln('RoboGuide 捕获回放 CLI\n'
      '  用法:\n'
      '    dart run tool/replay.dart meta --dir <捕获目录>\n'
      '    dart run tool/replay.dart spp  --dir <目录> --host <ip> [--port 60002] [--pace 毫秒] [--wait 毫秒]\n'
      '    dart run tool/replay.dart ws   --dir <目录> --host <ip> [--port 60002] [--liaison-port 50081] [--pace 毫秒] [--wait 毫秒] [--connect-ms 毫秒] [--no-grpc] [--session <id>]\n'
      '    dart run tool/replay.dart task --host <ip> --text "<转写文字>" [--port 50081] [--source N] [--context <json>] [--session <id>] [--wait 毫秒]\n'
      '\n'
      '  ⚠ ws 模式对真实机器人回放会驱动真实 ASR/Pilot/TTS；含运动指令的捕获可能让机器人动作。\n');
  exit(64);
}

String _require(Map<String, String> opts, String key) {
  final v = opts[key];
  if (v == null || v.isEmpty) {
    stderr.writeln('缺少参数 --$key');
    exit(64);
  }
  return v;
}

int _intOpt(Map<String, String> opts, String key, int def) =>
    int.tryParse(opts[key] ?? '') ?? def;

List<SppFrame> _readFrames(String dir) {
  final wire = File('$dir/stream.sppwire');
  if (!wire.existsSync()) {
    stderr.writeln('$dir 下没有 stream.sppwire');
    exit(66);
  }
  final framer = SppFramer();
  framer.add(wire.readAsBytesSync());
  return framer.takeFrames();
}

Map<String, dynamic> _readMeta(String dir) {
  final metaFile = File('$dir/meta.json');
  if (!metaFile.existsSync()) return <String, dynamic>{};
  try {
    final v = jsonDecode(metaFile.readAsStringSync());
    return v is Map<String, dynamic> ? v : <String, dynamic>{};
  } catch (_) {
    return <String, dynamic>{};
  }
}

void _logUndecoded(Uint8List bytes) {
  final hex = bytes.take(32).map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  stdout.writeln('[voice] undecodable: len=${bytes.length} head=$hex');
}

// ── meta ────────────────────────────────────────────────────────────
Future<void> _cmdMeta(Map<String, String> opts) async {
  final dir = _require(opts, 'dir');
  final meta = _readMeta(dir);
  print(jsonEncode(meta));
  final frames = _readFrames(dir);
  var audioBytes = 0;
  var controlFrames = 0;
  for (var i = 0; i < frames.length; i++) {
    final f = frames[i];
    final tag = f.type == SppFrameType.audio ? 'RGAD' : 'RGCT';
    print('[$i] $tag len=${f.payload.length}');
    if (f.type == SppFrameType.audio) {
      audioBytes += f.payload.length;
    } else {
      controlFrames++;
    }
  }
  print('frames=${frames.length} audioBytes=$audioBytes controlFrames=$controlFrames'
      ' pcmDurationMs=${meta['pcmBytes'] is int ? (meta['pcmBytes'] as int) * 1000 ~/ (16000 * 2) : '-'}');
}

// ── spp: 原样字节经 TCP 转发 ─────────────────────────────────────────
Future<void> _cmdSpp(Map<String, String> opts) async {
  final dir = _require(opts, 'dir');
  final host = _require(opts, 'host');
  final port = _intOpt(opts, 'port', 60002);
  final paceMs = _intOpt(opts, 'pace', 0);
  final waitMs = _intOpt(opts, 'wait', 2000);
  final frames = _readFrames(dir);

  // 逐帧发，字节与客户端写路径完全一致
  final socket = await Socket.connect(host, port);
  try {
    for (final f in frames) {
      final wire = f.type == SppFrameType.audio
          ? SppFramer.encodeAudio(f.payload)
          : SppFramer.encodeControl(jsonDecode(utf8.decode(f.payload)));
      socket.add(wire);
      if (paceMs > 0) {
        await socket.flush();
        await Future<void>.delayed(Duration(milliseconds: paceMs));
      }
    }
    await socket.flush();
    final rx = BytesBuilder();
    final sub = socket.listen(rx.add);
    await Future<void>.delayed(Duration(milliseconds: waitMs));
    await sub.cancel();
    print('spp replied bytes=${rx.length}');
  } finally {
    socket.destroy();
  }
}

// ── ws: 桥上行裸 PCM + mic_end，并经 Liaison gRPC 驱动真实语音会话 ───
Future<void> _cmdWs(Map<String, String> opts) async {
  final dir = _require(opts, 'dir');
  final host = _require(opts, 'host');
  final port = _intOpt(opts, 'port', 60002);
  final liaisonPort = _intOpt(opts, 'liaison-port', 50081);
  final paceMs = _intOpt(opts, 'pace', 0);
  final waitMs = _intOpt(opts, 'wait', 8000);
  final connectTimeout = Duration(
      milliseconds: _intOpt(opts, 'connect-ms', 8000));
  final useGrpc = !opts.containsKey('no-grpc');
  final frames = _readFrames(dir);
  final explicitSession = opts['session'];
  // 每次回放默认用全新 sessionId：重复复跑若沿用捕获包里的固定 id，机器人侧
  // 对同名会话会拒绝/掐断（首轮成功后那批会话仍挂在其 Liaison 上）。
  final sessionId = (explicitSession != null && explicitSession.isNotEmpty)
      ? explicitSession
      : 'replay-${DateTime.now().millisecondsSinceEpoch}';

  final channel = WebSocketChannel.connect(Uri.parse('ws://$host:$port/client'));
  try {
    await channel.ready.timeout(connectTimeout);
  } catch (e) {
    stderr.writeln('连不上 ws://$host:$port/client: $e');
    exit(1);
  }

  // gRPC 会话驱动：与 WsTransport.beginVoiceSession 同构。桥在收到上下行
  // 前会丢 PCM，必须先把语音会话起起来，音频才会真正进 ASR/Pilot。
  LiaisonClient? liaison;
  StreamSubscription<Uint8List>? voiceSub;
  var undecoded = 0;
  var naiveAcc = '';
  var mergedAcc = '';
  if (useGrpc) {
    liaison = LiaisonClient(host: host, port: liaisonPort);
    voiceSub = liaison
        .startVoiceSession(
          sessionId: sessionId,
          clientUserId: 'replay:roboguide',
          language: 'zh',
          ttsEnabled: true,
        )
        .listen((bytes) {
          final DecodedVoiceEvent ev;
          try {
            ev = decodeVoiceEventResponse(bytes);
          } catch (_) {
            undecoded++;
            _logUndecoded(bytes);
            return;
          }
          if (ev.eventKind == -1) {
            undecoded++;
            _logUndecoded(bytes);
            return;
          }
          final t = ev.text.isNotEmpty ? ev.text : '';
          final err = ev.error.isNotEmpty ? ev.error : '';
          final status = ev.statusMessage.isNotEmpty ? ev.statusMessage : '';
          final cf = ev.finalText.isNotEmpty ? ' final=${ev.finalText}' : '';
          final ck = ev.textChunk.isNotEmpty ? ' chunk=${ev.textChunk}' : '';
          stdout.writeln('[voice] kind=${ev.eventKind}${t.isNotEmpty ? ' text=$t' : ''}$cf$ck'
              '${err.isNotEmpty ? ' error=$err' : ''}${status.isNotEmpty ? ' status=$status' : ''}');
          if (ev.eventKind == 6 && t.isNotEmpty) {
            naiveAcc += t;
            mergedAcc = mergePilotText(mergedAcc, t);
          }
        }, onError: (Object e) => stdout.writeln('[voice] stream error: $e'));
  }

  var sentAudio = 0;
  var sentControl = 0;
  for (final f in frames) {
    if (f.type == SppFrameType.audio) {
      channel.sink.add(f.payload);
      sentAudio += f.payload.length;
    } else {
      try {
        channel.sink.add(utf8.decode(f.payload));
        sentControl++;
      } catch (_) {}
    }
    if (paceMs > 0) {
      await Future<void>.delayed(Duration(milliseconds: paceMs));
    }
  }

  var textEvents = 0;
  var rxAudio = 0;
  final sub = channel.stream.listen((m) {
    if (m is List<int>) {
      rxAudio += m.length;
      stdout.write('·');
    } else {
      textEvents++;
      stdout.writeln('<< $m');
    }
  });
  await Future<void>.delayed(Duration(milliseconds: waitMs));
  await sub.cancel();
  await channel.sink.close(ws_status.normalClosure).catchError((Object _) {});

  if (liaison != null) {
    await voiceSub?.cancel();
    try {
      final r = await liaison
          .finishVoiceCapture(sessionId)
          .timeout(const Duration(seconds: 12), onTimeout: () {
        stdout.writeln('[voice] finish timed out (12s)');
        return const FinishResult(true, 'client timed out waiting for finish ack');
      });
      stdout.writeln('[voice] finish ok=${r.ok} detail=${r.detail}');
    } catch (e) {
      stdout.writeln('[voice] finish failed: $e');
    }
    await liaison.close();
  }
  print('\nws session=$sessionId sent audioBytes=$sentAudio controlFrames=$sentControl'
      ' rxAudioBytes=$rxAudio textEvents=$textEvents undecoded=$undecoded');
  if (naiveAcc.isNotEmpty) {
    print('Pilot 文本对比\n'
        '  旧 naive +=: "$naiveAcc"\n'
        '  新 merge:    "$mergedAcc"');
  }
}

// ── task: 走引擎文字路径 SubmitTask，观察完整 PilotEvent 流 ─────────────
// 用于验证"工具调用后的 replan 叙述是否会在同一条流里继续发出"（判定多轮
// 回答在 voice/text 两条路径上到底有没有被截）。
Future<void> _cmdTask(Map<String, String> opts) async {
  final host = _require(opts, 'host');
  final port = _intOpt(opts, 'port', 50081);
  final text = _require(opts, 'text');
  final sessionId = opts['session'] ?? 'task-${DateTime.now().millisecondsSinceEpoch}';
  final waitMs = _intOpt(opts, 'wait', 90000);
  final source = _intOpt(opts, 'source', 0);
  final contextJson = opts['context'] ??
      '{"client":"roboguide-replay","modality":"text","interaction_mode":"task"}';
  final client = LiaisonClient(host: host, port: port);
  var events = 0;
  final done = Completer<void>();
  final sub = client
      .submitTask(
          sessionId: sessionId, text: text, contextJson: contextJson, source: source)
      .listen((bytes) {
    final ev = decodePilotEvent(bytes);
    events++;
    final t = ev.text;
    final st = ev.statusMessage.isNotEmpty ? ' [${ev.statusMessage}]' : '';
    stdout.writeln(
        '[pilot] ${pilotKindName(ev.eventKind)}(${ev.eventKind})${t.isNotEmpty ? ' text=$t' : ''}$st');
  }, onError: (Object e) {
    stdout.writeln('[pilot] stream error: $e');
    if (!done.isCompleted) done.complete();
  }, onDone: () {
    stdout.writeln('[pilot] stream ended: $events events');
    if (!done.isCompleted) done.complete();
  }, cancelOnError: true);
  await done.future.timeout(Duration(milliseconds: waitMs), onTimeout: () {
    stdout.writeln('[pilot] no stream end within ${waitMs}ms ($events events so far)');
  });
  await sub.cancel();
  await client.close();
  print('\ntask session=$sessionId text="$text" events=$events');
}