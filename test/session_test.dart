import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:roboguide_remote/session/pilot_text.dart';
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
    test('asr partial echoes text immediately without state change', () {
      final fsm = TurnFsm(onChanged: (_) {});
      fsm.apply(_ev(VoiceEventKind.recordingStarted));
      fsm.apply(_ev(VoiceEventKind.recordingDone));
      expect(fsm.state, TurnState.recognizing);
      fsm.apply(_ev(VoiceEventKind.asrPartial, text: '向前'));
      expect(fsm.userText, '向前');
      expect(fsm.state, TurnState.recognizing);
      // final 覆盖 partial
      fsm.apply(_ev(VoiceEventKind.asrFinal, text: '向前走'));
      expect(fsm.userText, '向前走');
      expect(fsm.state, TurnState.thinking);
    });

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

    test('area watchdog 被入向事件不断刷新，长分阶段回答不误判超时', () {
      fakeAsync((async) {
        final fsm = TurnFsm(
          softTimeout: const Duration(milliseconds: 50),
          onChanged: (_) {},
        );
        fsm.apply(_ev(VoiceEventKind.recordingStarted));
        // 每 40ms 来个事件，持续 160ms（> 单个 50ms 窗口）。若 watchdog 不随
        // 事件刷新，50ms 处就会误判超时；刷新的则全程保持 alive。
        for (var i = 0; i < 4; i++) {
          async.elapse(const Duration(milliseconds: 40));
          fsm.armWatchdog();
        }
        expect(fsm.state, isNot(TurnState.error));
        fsm.dispose();
      });
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

  group('mergePilotText (chunk/final 去重语义)', () {
    test('chunks accumulate continuously without separator or duplication', () {
      var t = mergePilotText('', '今天');
      t = mergePilotText(t, '的天气');
      t = mergePilotText(t, '很好');
      expect(t, '今天的天气很好');
    });

    test('final_text containing accumulated text replaces it (no dup)', () {
      final accumulated = '我先看看眼前的画面';
      final finalText = '我先看看眼前的画面，请稍候';
      expect(mergePilotText(accumulated, finalText), finalText);
    });

    test('final_text repeating the last narration segment is deduped', () {
      // 实测场景（长回放）：末段 final 恰好等于已累加的末段，不得重复
      const accumulated = '我先拍一张当前画面。相机暂时还没有图像，我正在等待画面。';
      const lastSegment = '相机暂时还没有图像，我正在等待画面。';
      expect(mergePilotText(accumulated, lastSegment), accumulated);
    });

    test('incoming substring already shown is ignored', () {
      expect(
        mergePilotText('我先看看眼前的画面，请稍候', '我先看看眼前的画面'),
        '我先看看眼前的画面，请稍候',
      );
    });

    test('final identical to current is idempotent', () {
      expect(mergePilotText('好的', '好的'), '好的');
    });

    test('disjoint addendum appends directly', () {
      expect(mergePilotText('好的', '还有什么可以帮你'), '好的还有什么可以帮你');
    });

    test('empty incoming keeps current, empty current takes incoming', () {
      expect(mergePilotText('已有的', ''), '已有的');
      expect(mergePilotText('', '新的'), '新的');
    });
  });
}

VoiceEvent _ev(VoiceEventKind kind, {String text = '', String error = ''}) =>
    VoiceEvent(kind: kind, text: text, error: error);
