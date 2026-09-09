import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'config/app_config.dart';
import 'ui/home_page.dart';

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
      home: const _HomeLoader(),
    );
  }
}

class _HomeLoader extends StatefulWidget {
  const _HomeLoader();

  @override
  State<_HomeLoader> createState() => _HomeLoaderState();
}

class _HomeLoaderState extends State<_HomeLoader> {
  AppSettings? _settings;
  String? _micBlocked;

  @override
  void initState() {
    super.initState();
    AppSettings.load().then((s) async {
      // Time-boxed like AppSettings.load: in widget tests the permission
      // channel never resolves and an unguarded await would hang the loader.
      // If the check itself fails, skip the banner — a real denial surfaces
      // at PTT time through the mic error path.
      String? blocked;
      try {
        final mic = await Permission.microphone.request()
            .timeout(const Duration(seconds: 2));
        if (!mic.isGranted) {
          blocked = '麦克风权限被拒绝，按住说话将无法录音。'
              '点击此横幅去系统设置 → 应用 → Roboguide → 权限，允许麦克风。';
        }
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _settings = s;
        _micBlocked = blocked;
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    final settings = _settings;
    if (settings == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    return HomePage(settings: settings, micBlockedNotice: _micBlocked);
  }
}
