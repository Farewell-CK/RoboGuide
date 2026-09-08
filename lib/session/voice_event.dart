/// Official VoiceEvent event_kind values (robonix Liaison contract,
/// see robonix-client-android LiaisonClient.mapVoiceEvent).
enum VoiceEventKind {
  sessionStarted(0),
  recordingStarted(1),
  recordingDone(2),
  asrPartial(3),
  asrFinal(4),
  userIdentified(5),
  pilot(6),
  ttsStarted(7),
  ttsDone(8),
  sessionDone(9),
  error(10),
  unknown(-1);

  final int value;
  const VoiceEventKind(this.value);

  static VoiceEventKind fromValue(int? raw) => VoiceEventKind.values
      .firstWhere((k) => k.value == raw, orElse: () => VoiceEventKind.unknown);
}

/// Decoded control event from the robot (`{type:'voice_event', ...}`).
class VoiceEvent {
  final VoiceEventKind kind;
  final String text;
  final String status;
  final String userId;
  final String error;

  const VoiceEvent({
    required this.kind,
    this.text = '',
    this.status = '',
    this.userId = '',
    this.error = '',
  });

  /// Parse from a control JSON map; null when the map is not a voice_event.
  static VoiceEvent? tryParse(Map<String, dynamic> value) {
    if (value['type'] != 'voice_event') return null;
    return VoiceEvent(
      kind: VoiceEventKind.fromValue((value['event_kind'] as num?)?.toInt()),
      text: value['text'] as String? ?? '',
      status: value['status'] as String? ?? '',
      userId: value['user_id'] as String? ?? '',
      error: value['error'] as String? ?? '',
    );
  }
}
