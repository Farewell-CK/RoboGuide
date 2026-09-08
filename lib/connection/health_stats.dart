import 'dart:typed_data';

/// Counters for the connection/audio health panel. Classic Bluetooth offers
/// no reliable RSSI on Android, so link quality is judged from RX cadence.
class HealthStats {
  int txAudioBytes = 0;
  int rxAudioBytes = 0;
  int txControlMessages = 0;
  int rxControlMessages = 0;
  int reconnects = 0;

  DateTime? _lastRxAt;
  final _recentRxAudioGaps = <Duration>[];

  /// Record an inbound audio frame; tracks inter-frame gaps.
  void onRxAudio(Uint8List frame) {
    rxAudioBytes += frame.length;
    final now = DateTime.now();
    final last = _lastRxAt;
    _lastRxAt = now;
    if (last != null) {
      _recentRxAudioGaps.add(now.difference(last));
      if (_recentRxAudioGaps.length > 20) _recentRxAudioGaps.removeAt(0);
    }
  }

  void onTxControl() => txControlMessages++;
  void onRxControl() => rxControlMessages++;

  void onReconnect() => reconnects++;

  /// Mean gap between received audio frames. Short = healthy downlink.
  Duration? get meanRxAudioGap {
    if (_recentRxAudioGaps.isEmpty) return null;
    var total = Duration.zero;
    for (final g in _recentRxAudioGaps) {
      total += g;
    }
    return Duration(microseconds: total.inMicroseconds ~/ _recentRxAudioGaps.length);
  }

  /// Link quality grade from RX cadence: good < 150ms, fair < 400ms, poor otherwise.
  String get linkQuality {
    final gap = meanRxAudioGap;
    if (gap == null) return '-';
    if (gap.inMilliseconds < 150) return 'good';
    if (gap.inMilliseconds < 400) return 'fair';
    return 'poor';
  }

  void reset() {
    txAudioBytes = 0;
    rxAudioBytes = 0;
    txControlMessages = 0;
    rxControlMessages = 0;
    reconnects = 0;
    _recentRxAudioGaps.clear();
    _lastRxAt = null;
  }
}
