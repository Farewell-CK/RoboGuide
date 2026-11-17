import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:indoor_navigation_app/services/navigation_prompts.dart';
import 'package:indoor_navigation_app/widgets/navigation_prompt_panel.dart';

void main() {
  testWidgets(
    'missing Chinese voice keeps subtitles and replay retries configuration',
    (tester) async {
      var chineseVoiceInstalled = false;
      final spoken = <String>[];
      final prompts = NavigationPrompts(
        configure: () async {
          if (!chineseVoiceInstalled) throw StateError('Chinese voice missing');
        },
        stop: () async => 1,
        speak: (text) async {
          spoken.add(text);
          return 1;
        },
      );
      prompts.show('朝向正确，请保持这个方向。');
      await tester.pump();
      expect(prompts.voiceError, isNotNull);
      expect(spoken, isEmpty);
      expect(prompts.text, contains('朝向正确'));
      chineseVoiceInstalled = true;
      prompts.repeat();
      await tester.pump();
      expect(spoken, ['朝向正确，请保持这个方向。']);
      expect(prompts.voiceError, isNull);
      prompts.dispose();
      await tester.pump();
    },
  );
  testWidgets(
    'latest prompt replaces an obsolete prompt while audio initializes',
    (tester) async {
      final ready = Completer<void>();
      final spoken = <String>[];
      final prompts = NavigationPrompts(
        configure: () => ready.future,
        stop: () async => 1,
        speak: (text) async {
          spoken.add(text);
          return 1;
        },
      );
      prompts.show('准备校准出发方向');
      prompts.show('朝向已确认，可以出发');
      expect(prompts.text, '朝向已确认，可以出发');
      expect(spoken, isEmpty);
      ready.complete();
      await tester.pump();
      expect(spoken, ['朝向已确认，可以出发']);
      prompts.dispose();
      await tester.pump();
    },
  );

  testWidgets(
    'repeated errors are throttled but manual replay and mute still work',
    (tester) async {
      final spoken = <String>[];
      final prompts = NavigationPrompts(
        configure: () async {},
        stop: () async => 1,
        speak: (text) async {
          spoken.add(text);
          return 1;
        },
      );
      prompts.show('请检查连接', kind: NavigationPromptKind.warning);
      await tester.pump();
      prompts.show('请检查连接', kind: NavigationPromptKind.warning);
      await tester.pump();
      expect(spoken, hasLength(1));
      prompts.repeat();
      await tester.pump();
      expect(spoken, hasLength(2));
      prompts.toggleVoice();
      prompts.show('定位成功，请选择目的地');
      await tester.pump();
      expect(prompts.text, '定位成功，请选择目的地');
      expect(spoken, hasLength(2));
      prompts.toggleVoice();
      await tester.pump();
      expect(spoken.last, '定位成功，请选择目的地');
      prompts.dispose();
      await tester.pump();
    },
  );

  testWidgets(
    'turn remains visible across adjacent straight references; arrival stays until reset',
    (tester) async {
      var now = DateTime(2026, 11, 10);
      final spoken = <String>[];
      final prompts = NavigationPrompts(
        configure: () async {},
        stop: () async => 1,
        speak: (text) async {
          spoken.add(text);
          return 1;
        },
        clock: () => now,
      );
      prompts.show('请左转', kind: NavigationPromptKind.turn);
      prompts.show('继续直行');
      await tester.pump();
      expect(prompts.text, '请左转');
      expect(spoken, ['请左转']);
      now = now.add(const Duration(seconds: 5));
      prompts.show('继续直行');
      await tester.pump();
      expect(prompts.text, '继续直行');
      prompts.show('已到达电梯口，请停下', kind: NavigationPromptKind.arrival);
      prompts.show('继续直行');
      prompts.show('连接失败', kind: NavigationPromptKind.warning);
      expect(prompts.text, '已到达电梯口，请停下');
      await tester.pump();
      expect(spoken.last, '已到达电梯口，请停下');
      prompts.reset();
      prompts.show('开始新的定位');
      await tester.pump();
      expect(spoken.last, '开始新的定位');
      prompts.dispose();
      await tester.pump();
    },
  );

  testWidgets(
    'hanging audio cannot block arrival subtitles and background drops queued speech',
    (tester) async {
      final hung = Completer<dynamic>();
      final spoken = <String>[];
      var hangStop = true;
      final prompts = NavigationPrompts(
        configure: () async {},
        stop: () => hangStop ? hung.future : Future.value(1),
        speak: (text) async {
          spoken.add(text);
          return 1;
        },
      );
      prompts.show('继续直行');
      await tester.pump();
      prompts.show('已到达电梯口', kind: NavigationPromptKind.arrival);
      expect(prompts.text, '已到达电梯口');
      prompts.suspend();
      hangStop = false;
      hung.complete(1);
      await tester.pump();
      expect(spoken, isEmpty);
      prompts.resume();
      prompts.repeat();
      await tester.pump();
      expect(spoken, ['已到达电梯口']);
      prompts.dispose();
      await tester.pump();
    },
  );

  testWidgets(
    'speech failure leaves readable subtitles and an explicit fallback',
    (tester) async {
      final prompts = NavigationPrompts(
        configure: () async {},
        stop: () async => 1,
        speak: (_) async => throw StateError('no voice engine'),
      );
      prompts.show('正在定位，请缓慢扫描');
      await tester.pump();
      expect(prompts.text, '正在定位，请缓慢扫描');
      expect(prompts.voiceError, contains('中文语音引擎'));
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: NavigationPromptPanel(prompts: prompts)),
        ),
      );
      expect(find.text('正在定位，请缓慢扫描'), findsOneWidget);
      expect(find.textContaining('语音暂不可用'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      prompts.dispose();
      await tester.pump();
    },
  );

  testWidgets(
    'small-screen enlarged subtitles retain reachable mute, replay and stop controls',
    (tester) async {
      tester.view.physicalSize = const Size(320, 568);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var stopped = false;
      final prompts = NavigationPrompts(
        configure: () async {},
        stop: () async => 1,
        speak: (_) async => 1,
      );
      prompts.show(
        '暂时无法获取导航画面，请停下并检查服务器连接。',
        kind: NavigationPromptKind.warning,
      );
      prompts.reportVoiceError();
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: const MediaQueryData(
              size: Size(320, 568),
              textScaler: TextScaler.linear(2),
            ),
            child: Scaffold(
              body: Align(
                alignment: Alignment.bottomCenter,
                child: NavigationPromptLayout(
                  prompts: prompts,
                  details: Text(List.filled(30, '路线详情').join('\n')),
                  actions: ElevatedButton(
                    onPressed: () => stopped = true,
                    child: const Text('停止导航'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pump();
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('语音已开启'));
      await tester.pump();
      expect(prompts.voiceEnabled, isFalse);
      await tester.tap(find.text('语音已关闭'));
      await tester.pump();
      await tester.tap(find.text('重播提示'));
      await tester.tap(find.text('停止导航'));
      expect(stopped, isTrue);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      prompts.dispose();
      await tester.pump();
    },
  );
}
