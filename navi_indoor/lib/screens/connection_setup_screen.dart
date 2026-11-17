import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// Keep project-specific connection details out of the distributed APK.
class ConnectionSetupApp extends StatelessWidget {
  const ConnectionSetupApp({super.key, required this.onStart});
  final Future<void> Function() onStart;

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'NAVI',
      locale: const Locale('zh', 'CN'),
      supportedLocales: const [Locale('zh', 'CN')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: ThemeData(colorSchemeSeed: Colors.blue, useMaterial3: true),
    home: _ConnectionSetupScreen(onStart: onStart),
  );
}

class _ConnectionSetupScreen extends StatefulWidget {
  const _ConnectionSetupScreen({required this.onStart});
  final Future<void> Function() onStart;

  @override
  State<_ConnectionSetupScreen> createState() => _ConnectionSetupScreenState();
}

class _ConnectionSetupScreenState extends State<_ConnectionSetupScreen> {
  final _vision = TextEditingController();
  final _database = TextEditingController();
  final _key = TextEditingController();
  bool _busy = true;
  String _status = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!mounted) return;
      _vision.text = prefs.getString('VISION_SERVER_URL') ?? '';
      _database.text = prefs.getString('SUPABASE_URL') ?? '';
      _key.text = prefs.getString('SUPABASE_ANON_KEY') ?? '';
    } catch (_) {
      _status = '无法读取已保存配置，请重新填写。';
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  void dispose() {
    _vision.dispose();
    _database.dispose();
    _key.dispose();
    super.dispose();
  }

  String _url(TextEditingController controller) =>
      controller.text.trim().replaceFirst(RegExp(r'/+$'), '');

  bool _validUrl(String value) {
    final uri = Uri.tryParse(value);
    return uri != null &&
        (uri.scheme == 'http' || uri.scheme == 'https') &&
        uri.host.isNotEmpty &&
        uri.userInfo.isEmpty &&
        !uri.hasQuery &&
        !uri.hasFragment;
  }

  Future<void> _testServer() async {
    final url = _url(_vision);
    if (!_validUrl(url)) {
      setState(() => _status = '请填写完整服务器地址，例如 http://服务器地址:8000');
      return;
    }
    setState(() {
      _busy = true;
      _status = '正在检查服务器…';
    });
    try {
      final response = await http
          .get(Uri.parse('$url/health'))
          .timeout(const Duration(seconds: 12));
      if (mounted) {
        setState(
          () =>
              _status =
                  response.statusCode == 200
                      ? '服务器可以连接。录制与导航还需要下方的数据库配置。'
                      : '服务器返回 HTTP ${response.statusCode}，请核对地址。',
        );
      }
    } catch (_) {
      if (mounted) setState(() => _status = '无法连接服务器。请检查地址、端口转发和网络。');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _start() async {
    if (!_validUrl(_url(_vision)) ||
        !_validUrl(_url(_database)) ||
        _key.text.trim().isEmpty) {
      setState(() => _status = '请填写服务器地址、Supabase 项目地址和公开客户端密钥。');
      return;
    }
    setState(() {
      _busy = true;
      _status = '正在启动…';
    });
    try {
      final prefs = await SharedPreferences.getInstance();
      final settings = {
        'VISION_SERVER_URL': _url(_vision),
        'SUPABASE_URL': _url(_database),
        'SUPABASE_ANON_KEY': _key.text.trim(),
      };
      for (final entry in settings.entries) {
        await prefs.setString(entry.key, entry.value);
        dotenv.env[entry.key] = entry.value;
      }
      await widget.onStart();
    } catch (_) {
      if (mounted) setState(() => _status = '启动失败。请核对数据库配置、网络与相机权限，关闭后重试。');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('NAVI 连接设置')),
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const Text(
            '室内导航演示版',
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 12),
          const Text('先连接 DINOv2 服务器，再使用项目数据库登录、录制路线和导航。每次打开都可修改配置。'),
          const SizedBox(height: 24),
          TextField(
            controller: _vision,
            enabled: !_busy,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'DINOv2 服务器地址',
              hintText: 'http://服务器地址:8000',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          OutlinedButton(
            onPressed: _busy ? null : _testServer,
            child: const Text('测试服务器连接'),
          ),
          const SizedBox(height: 20),
          TextField(
            controller: _database,
            enabled: !_busy,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'Supabase 项目地址',
              hintText: 'https://项目.supabase.co',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _key,
            enabled: !_busy,
            obscureText: true,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'Supabase anon / publishable key',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          const Text('向项目负责人获取数据库配置；仅填写公开客户端密钥。此版本不附带数据库、路线数据或 GPT 密钥。'),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: _busy ? null : _start,
            child: const Text('保存并进入 NAVI'),
          ),
          const SizedBox(height: 16),
          if (_busy) const Center(child: CircularProgressIndicator()),
          if (_status.isNotEmpty)
            Text(_status, style: const TextStyle(fontSize: 16)),
        ],
      ),
    ),
  );
}
