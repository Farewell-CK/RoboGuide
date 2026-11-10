import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:indoor_navigation_app/screens/connection_setup_screen.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    dotenv.testLoad(fileInput: '');
  });

  testWidgets('missing backend configuration keeps the app on setup', (
    tester,
  ) async {
    var starts = 0;
    await tester.pumpWidget(
      ConnectionSetupApp(
        onStart: () async {
          starts++;
        },
      ),
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('保存并进入 NAVI'));
    await tester.tap(find.text('保存并进入 NAVI'));
    await tester.pumpAndSettle();
    expect(starts, 0);
    expect(find.text('请填写服务器地址、Supabase 项目地址和公开客户端密钥。'), findsOneWidget);
  });

  testWidgets('saved connections are restored and passed to navigation', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({
      'VISION_SERVER_URL': 'http://10.0.0.2:8000/',
      'SUPABASE_URL': 'https://example.supabase.co/',
      'SUPABASE_ANON_KEY': 'test-public-key',
    });
    var started = false;
    await tester.pumpWidget(
      ConnectionSetupApp(
        onStart: () async {
          started = true;
        },
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.widgetWithText(TextField, 'http://10.0.0.2:8000/'),
      findsOneWidget,
    );
    await tester.ensureVisible(find.text('保存并进入 NAVI'));
    await tester.tap(find.text('保存并进入 NAVI'));
    await tester.pumpAndSettle();
    expect(started, isTrue);
    expect(dotenv.env['VISION_SERVER_URL'], 'http://10.0.0.2:8000');
    expect(dotenv.env['SUPABASE_URL'], 'https://example.supabase.co');
    expect(dotenv.env['SUPABASE_ANON_KEY'], 'test-public-key');
  });
}
