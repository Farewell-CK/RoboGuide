/// App-wide configuration.
class AppConfig {
  /// Default robot bridge address. Overridable from the UI.
  ///
  /// Note: prefer the robot's LAN IP when the phone is on a route to it
  /// (avoids Tailscale DERP issues). `100.72.167.58` is Thor's Tailscale IP
  /// and is used only when the Tun/Tailscale path works.
  static const String defaultRobotHost = '192.168.1.33'; // Thor LAN IP
  static const int defaultBridgePort = 60002;

  /// Where the robonix Atlas lives (for future control-plane features).
  static const String atlasHost = '100.72.167.58';
  static const int atlasPort = 50051;
}
