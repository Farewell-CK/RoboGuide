import 'package:flutter/material.dart';

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

  @override
  void initState() {
    super.initState();
    AppSettings.load().then((s) {
      if (mounted) setState(() => _settings = s);
    });
  }

  @override
  Widget build(BuildContext context) {
    final settings = _settings;
    if (settings == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    return HomePage(settings: settings);
  }
}
