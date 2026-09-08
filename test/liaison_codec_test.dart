import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:roboguide_remote/grpc/liaison_codec.dart';

/// Helper: build a protobuf wire buffer from (field, wire, value) triples
/// so we can assert against independently-constructed bytes.
Uint8List _pb(List<int> bytes) => Uint8List.fromList(bytes);

void main() {
  group('StartVoiceSession_Request encoding', () {
    test('encodes fields per liaison.proto and skips defaults', () {
      final bytes = encodeStartVoiceSessionRequest(
        sessionId: 's1',
        clientUserId: 'u1',
        recordSeconds: 30,
        language: 'zh',
        ttsEnabled: true,
        contextJson: '{"a":1}',
      );
      // field1 (s1) = 0x0A len2, field2 (u1) = 0x12 len2, field3 (30) =
      // 0x18 0x1E, field4 (zh) = 0x22 len2, field5 (true) = 0x28 0x01,
      // field11 = 0x5A len7
      final expected = _pb([
        0x0A, 2, 0x73, 0x31, // s1
        0x12, 2, 0x75, 0x31, // u1
        0x18, 0x1E, // 30
        0x22, 2, 0x7A, 0x68, // zh
        0x28, 0x01, // tts_enabled
        0x5A, 7, 0x7B, 0x22, 0x61, 0x22, 0x3A, 0x31, 0x7D, // {"a":1}
      ]);
      expect(bytes, expected);
    });

    test('omits default values (empty strings, recordSeconds=0, tts=false)', () {
      final bytes = encodeStartVoiceSessionRequest(
        sessionId: '',
        clientUserId: '',
        recordSeconds: 0,
        language: '',
        ttsEnabled: false,
        contextJson: '',
      );
      expect(bytes, isEmpty);
    });
  });

  group('VoiceEvent decoding', () {
    test('decodes a bare VoiceEvent (event_kind varint at top level)', () {
      // VoiceEvent{ event_kind=4, text='你好' }: field1 varint, field3 bytes
      // utf8 '你好' = E4 BD A0 E5 A5 BD
      final bytes = _pb([
        0x08, 0x04, // event_kind=4
        0x1A, 6, 0xE4, 0xBD, 0xA0, 0xE5, 0xA5, 0xBD, // text
      ]);
      final event = decodeVoiceEventResponse(bytes);
      expect(event.eventKind, 4);
      expect(event.text, '你好');
    });

    test('decodes a wrapped StartVoiceSession_Response', () {
      // Response{ event: VoiceEvent{ event_kind=9, session_id='sess' } }
      final inner = _pb([
        0x08, 0x09, // event_kind=9
        0x12, 4, 0x73, 0x65, 0x73, 0x73, // session_id 'sess'
      ]);
      final wrapped = _pb([0x0A, inner.length, ...inner]);
      final event = decodeVoiceEventResponse(wrapped);
      expect(event.eventKind, 9);
      expect(event.sessionId, 'sess');
    });

    test('pilot nested message: final_text wins over text_chunk', () {
      // VoiceEvent{ event_kind=6, pilot: PilotEvent{ text_chunk='ab',
      // final_text='xy' } }
      final pilot = _pb([
        0x1A, 2, 0x61, 0x62, // field3 text_chunk 'ab'
        0x3A, 2, 0x78, 0x79, // field7 final_text 'xy'
      ]);
      final bytes = _pb([
        0x08, 0x06, // event_kind=6
        0x32, pilot.length, ...pilot, // field6 pilot
      ]);
      final event = decodeVoiceEventResponse(bytes);
      expect(event.eventKind, 6);
      expect(event.text, 'xy');
    });

    test('empty buffer decodes to unknown kind', () {
      expect(decodeVoiceEventResponse(Uint8List(0)).eventKind, -1);
    });
  });

  group('FinishVoiceCapture round trip', () {
    test('request/response encode+decode', () {
      final req = encodeFinishVoiceCaptureRequest('sess9');
      expect(req, _pb([0x0A, 5, 0x73, 0x65, 0x73, 0x73, 0x39]));

      // Response{ ok=true, detail='done' }
      final resp = _pb([
        0x08, 0x01, // ok
        0x1A, 4, 0x64, 0x6F, 0x6E, 0x65, // 'done'
      ]);
      final result = decodeFinishVoiceCaptureResponse(resp);
      expect(result.ok, isTrue);
      expect(result.detail, 'done');
    });
  });
}
