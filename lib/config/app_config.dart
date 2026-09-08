import 'package:shared_preferences/shared_preferences.dart';

import '../transport/robot_transport.dart';

/// App-wide configuration & persisted settings.
class AppSettings {
  static const defaultThorMac = 'F8:3D:C6:91:8D:69';
  static const defaultWsHost = '192.168.1.33';
  static const defaultWsPort = 60002;

  TransportMode mode;
  String mac;
  String wsHost;
  int wsPort;
  int liaisonPort;
  bool autoReconnect;

  AppSettings({
    this.mode = TransportMode.spp,
    this.mac = defaultThorMac,
    this.wsHost = defaultWsHost,
    this.wsPort = defaultWsPort,
    this.liaisonPort = 50081,
    this.autoReconnect = true,
  });

  static const _key = 'roboguide.settings_v3';

  static Future<AppSettings> load() async {
    final s = AppSettings();
    try {
      // Time-boxed: in widget tests the method channel may never resolve and
      // an uncaught pending future would leave the loader spinner forever.
      final prefs = await SharedPreferences.getInstance()
          .timeout(const Duration(seconds: 2));
      final modeIndex = prefs.getInt('$_key.mode');
      if (modeIndex != null && modeIndex >= 0 && modeIndex < TransportMode.values.length) {
        s.mode = TransportMode.values[modeIndex];
      }
      s.mac = prefs.getString('$_key.mac') ?? s.mac;
      s.wsHost = prefs.getString('$_key.wsHost') ?? s.wsHost;
      s.wsPort = prefs.getInt('$_key.wsPort') ?? s.wsPort;
      s.liaisonPort = prefs.getInt('$_key.liaisonPort') ?? s.liaisonPort;
      s.autoReconnect = prefs.getBool('$_key.autoReconnect') ?? s.autoReconnect;
    } catch (_) {
      // plugin unavailable (e.g. widget tests) — keep defaults
    }
    return s;
  }

  Future<void> save() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setInt('$_key.mode', mode.index);
      await prefs.setString('$_key.mac', mac);
      await prefs.setString('$_key.wsHost', wsHost);
      await prefs.setInt('$_key.wsPort', wsPort);
      await prefs.setInt('$_key.liaisonPort', liaisonPort);
      await prefs.setBool('$_key.autoReconnect', autoReconnect);
    } catch (_) {}
  }
}
