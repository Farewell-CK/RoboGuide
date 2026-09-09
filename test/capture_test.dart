import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:roboguide_remote/capture/capture_controller.dart';
import 'package:roboguide_remote/transport/spp_framer.dart';

Uint8List _pcm(int n, [int seed = 0]) =>
    Uint8List.fromList(List.generate(n, (i) => (i + seed) % 256));

void main() {
  late Directory base;
  late CaptureController capture;
  final errors = <String>[];

  setUp(() {
    base = Directory.systemTemp.createTempSync('rg_capture_test_');
    capture = CaptureController(
      baseDir: base,
      enabled: true,
      onError: errors.add,
    );
    errors.clear();
  });

  tearDown(() => base.deleteSync(recursive: true));

  test('captures exact SPP wire bytes + pcm + meta per turn', () async {
    final c1 = _pcm(3200, 1);
    final c2 = _pcm(3200, 2);
    final c3 = _pcm(800, 3);

    final session = await capture.begin(sessionId: 's1', mode: 'spp');
    expect(session, isNotNull);
    session!
      ..onPcm(c1)
      ..onPcm(c2)
      ..onPcm(c3);
    final dir = await capture.endActive();
    expect(dir, isNotNull);

    final capDir = Directory(dir!);
    expect(capDir.existsSync(), isTrue);
    expect(File('$dir/stream.sppwire').existsSync(), isTrue);
    expect(File('$dir/audio.pcm').existsSync(), isTrue);
    expect(File('$dir/meta.json').existsSync(), isTrue);

    // .sppwire 是真实客户端会写出的 RGAD 音频帧 + 末尾 RGCT mic_end
    final framer = SppFramer();
    framer.add(File('$dir/stream.sppwire').readAsBytesSync());
    final frames = framer.takeFrames();
    expect(frames.length, 3 + 1);
    final audio = frames.where((f) => f.type == SppFrameType.audio).toList();
    expect(audio.map((f) => List<int>.of(f.payload)),
        [c1.toList(), c2.toList(), c3.toList()]);
    final endCtl = frames.last;
    expect(endCtl.type, SppFrameType.control);
    expect(jsonDecode(utf8.decode(endCtl.payload)), {'type': 'mic_end', 'stream_id': ''});

    // .pcm 为裸字节拼接
    final pcm = File('$dir/audio.pcm').readAsBytesSync();
    expect(pcm, [c1, c2, c3].expand((b) => b).toList());

    final meta = jsonDecode(File('$dir/meta.json').readAsStringSync()); // dynamic
    final m = (meta as Map).cast<String, dynamic>();
    expect(m['chunkCount'], 3);
    expect(m['pcmBytes'], c1.length + c2.length + c3.length);
    expect(m['micEnd'], isTrue);
    expect(m['mode'], 'spp');
    expect(m['sampleRate'], 16000);
    expect(m['wireBytes'],
        c1.length + c2.length + c3.length + 3 * SppFramer.headerBytes +
            SppFramer.encodeControl({'type': 'mic_end', 'stream_id': ''}).length);
  });

  test('second turn gets its own folder; wireByte/pcm byte counts reset', () async {
    final s1 = await capture.begin(sessionId: 't1', mode: 'spp');
    s1!.onPcm(_pcm(100));
    await capture.endActive();
    final s2 = await capture.begin(sessionId: 't2', mode: 'spp');
    s2!.onPcm(_pcm(200));
    final dir2 = (await capture.endActive())!;

    final meta2 = jsonDecode(File('$dir2/meta.json').readAsStringSync());
    expect((meta2 as Map)['pcmBytes'], 200);
    final dirs = Directory('${base.path}/captures')
        .listSync()
        .whereType<Directory>()
        .toList();
    expect(dirs.length, 2);
  });

  test('begin returns null when disabled and never writes', () async {
    capture.enabled = false;
    final s = await capture.begin(sessionId: 'x', mode: 'spp');
    expect(s, isNull);
    expect(Directory('${base.path}/captures').existsSync(), isFalse);
  });

  test('endActive is safe with no active session', () async {
    expect(await capture.endActive(), isNull);
    expect(errors, isEmpty);
  });

  test('io failure surfaces via onError, not a throw', () async {
    // captures 路径上已存在同名文件 → createSync 必失败
    final baseDir = Directory('${base.path}/io_fail')..createSync(recursive: true);
    File('${baseDir.path}/captures').writeAsStringSync('x');
    final bad = CaptureController(baseDir: baseDir, enabled: true, onError: errors.add);
    final s = await bad.begin(sessionId: 'x', mode: 'spp');
    expect(s, isNull);
    expect(errors, isNotEmpty);
  });
}