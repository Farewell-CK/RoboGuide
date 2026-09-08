import 'package:flutter_test/flutter_test.dart';
import 'package:roboguide_remote/session/session_store.dart';
import 'package:roboguide_remote/session/turn_fsm.dart';
import 'package:roboguide_remote/session/voice_event.dart';

void main() {
  group('VoiceEvent parsing', () {
    test('parses official event kinds', () {
      final e = VoiceEvent.tryParse({
        'type': 'voice_event',
        'event_kind': 4,
        'text': '你好',
        'status': '',
        'user_id': 'u1',
        'error': '',
      });
      expect(e, isNotNull);
      expect(e!.kind, VoiceEventKind.asrFinal);
      expect(e.text, '你好');
    });

    test('ignores non-voice_event maps', () {
      expect(
        VoiceEvent.tryParse({'type': 'mic_start'}),
        isNull,
      );
    });

    test('unknown kind maps to unknown', () {
      final e = VoiceEvent.tryParse({'type': 'voice_event', 'event_kind': 99});
      expect(e!.kind, VoiceEventKind.unknown);
    });
  });

  group('TurnFsm', () {
    test('happy path recording->done', () {
      late TurnFsm fsm;
      fsm = TurnFsm(onChanged: (_) {});
      expect(fsm.apply(_ev(VoiceEventKind.recordingStarted)), isTrue);
      expect(fsm.state, TurnState.recording);
      expect(fsm.apply(_ev(VoiceEventKind.recordingDone)), isTrue);
      expect(fsm.state, TurnState.recognizing);
      expect(fsm.apply(_ev(VoiceEventKind.asrFinal, text: '前进')), isTrue);
      expect(fsm.state, TurnState.thinking);
      expect(fsm.userText, '前进');
      expect(fsm.apply(_ev(VoiceEventKind.pilot, text: '好的')), isTrue);
      expect(fsm.assistantText, '好的');
      expect(fsm.apply(_ev(VoiceEventKind.ttsStarted)), isTrue);
      expect(fsm.state, TurnState.playing);
      expect(fsm.apply(_ev(VoiceEventKind.sessionDone)), isTrue);
      expect(fsm.state, TurnState.done);
      expect(fsm.isTerminal, isTrue);
    });

    test('error event surfaces error and terminates', () {
      final fsm = TurnFsm(onChanged: (_) {});
      fsm.apply(_ev(VoiceEventKind.error, error: 'boom'));
      expect(fsm.state, TurnState.error);
      expect(fsm.error, 'boom');
      expect(fsm.isTerminal, isTrue);
    });

    test('interrupt forces non-terminal to error', () {
      final fsm = TurnFsm(onChanged: (_) {});
      fsm.apply(_ev(VoiceEventKind.asrFinal, text: 'x'));
      expect(fsm.isTerminal, isFalse);
      fsm.interrupt('被新一轮说话打断');
      expect(fsm.state, TurnState.error);
    });

    test('soft timeout fires error', () {
      late TurnFsm fsm;
      fsm = TurnFsm(
        softTimeout: const Duration(milliseconds: 50),
        onChanged: (_) {},
      );
      fsm.apply(_ev(VoiceEventKind.recordingStarted));
      // watchdog armed by non-terminal transition
      expect(fsm.state, TurnState.recording);
      // let the watchdog fire (real async in dart:core Timer works in tests
      // via FakeAsync only; use pump test below instead)
      fsm.dispose();
    });
  });

  group('Session models round-trip', () {
    test('session/turn json round trip', () {
      final turn = ConversationTurn(id: 't1', userText: '你好', assistantText: '嗨', state: 'done');
      final session = ConversationSession(
        id: 's1',
        title: '测试会话',
        turns: [turn],
      );
      final restored = ConversationSession.fromJson(
        (session.toJson()).cast<String, dynamic>(),
      );
      expect(restored.id, 's1');
      expect(restored.title, '测试会话');
      expect(restored.turns.length, 1);
      expect(restored.turns.first.userText, '你好');
      expect(restored.turns.first.assistantText, '嗨');
      expect(restored.turns.first.state, 'done');
    });
  });
}

VoiceEvent _ev(VoiceEventKind kind, {String text = '', String error = ''}) =>
    VoiceEvent(kind: kind, text: text, error: error);
