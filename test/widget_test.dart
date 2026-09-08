import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:roboguide_remote/app.dart';
import 'package:roboguide_remote/config/app_config.dart';
import 'package:roboguide_remote/ui/home_page.dart';

void main() {
  testWidgets('App renders home page', (WidgetTester tester) async {
    // Provide an in-memory settings store so AppSettings.load resolves.
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const RoboguideRemoteApp());
    // AppSettings.load has a 2s timeout fallback; push fake time past it in
    // case the mock channel does not resolve promptly.
    await tester.pump(const Duration(seconds: 3));

    expect(find.text('Roboguide Remote'), findsOneWidget);
    expect(find.text('连接'), findsOneWidget);
  });

  testWidgets('HomePage renders with default settings',
      (WidgetTester tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(MaterialApp(
      home: HomePage(settings: AppSettings()),
    ));
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));

    expect(find.text('Roboguide Remote'), findsOneWidget);
  });
}
