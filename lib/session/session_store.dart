import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// One PTT press->release round-trip.
class ConversationTurn {
  final String id;
  final DateTime startedAt;
  String userText;
  String assistantText;
  String state; // recording | recognizing | thinking | playing | done | error
  String error;

  ConversationTurn({
    required this.id,
    DateTime? startedAt,
    this.state = 'recording',
    this.userText = '',
    this.assistantText = '',
    this.error = '',
  }) : startedAt = startedAt ?? DateTime.now();

  Map<String, dynamic> toJson() => {
        'id': id,
        'startedAt': startedAt.toIso8601String(),
        'userText': userText,
        'assistantText': assistantText,
        'state': state,
        'error': error,
      };

  factory ConversationTurn.fromJson(Map<String, dynamic> json) =>
      ConversationTurn(
        id: json['id'] as String? ?? '',
        startedAt: DateTime.tryParse(json['startedAt'] as String? ?? ''),
        userText: json['userText'] as String? ?? '',
        assistantText: json['assistantText'] as String? ?? '',
        state: json['state'] as String? ?? 'done',
        error: json['error'] as String? ?? '',
      );
}

/// A conversation: a list of turns (newest first).
class ConversationSession {
  final String id;
  final DateTime createdAt;
  String title;
  DateTime lastActiveAt;
  final List<ConversationTurn> turns;

  ConversationSession({
    required this.id,
    DateTime? createdAt,
    this.title = '新会话',
    DateTime? lastActiveAt,
    List<ConversationTurn>? turns,
  })  : createdAt = createdAt ?? DateTime.now(),
        lastActiveAt = lastActiveAt ?? DateTime.now(),
        turns = turns ?? [];

  Map<String, dynamic> toJson() => {
        'id': id,
        'createdAt': createdAt.toIso8601String(),
        'title': title,
        'lastActiveAt': lastActiveAt.toIso8601String(),
        'turns': turns.map((t) => t.toJson()).toList(),
      };

  factory ConversationSession.fromJson(Map<String, dynamic> json) {
    final rawTurns = json['turns'] as List? ?? [];
    return ConversationSession(
      id: json['id'] as String? ?? '',
      createdAt: DateTime.tryParse(json['createdAt'] as String? ?? ''),
      title: json['title'] as String? ?? '新会话',
      lastActiveAt: DateTime.tryParse(json['lastActiveAt'] as String? ?? ''),
      turns: rawTurns
          .cast<Map<String, dynamic>>()
          .map(ConversationTurn.fromJson)
          .toList(),
    );
  }
}

/// shared_preferences persistence for the session tree.
class SessionStore {
  static const _sessionsKey = 'roboguide.sessions_v2';
  static const _currentKey = 'roboguide.current_session_v2';
  static const int maxSessions = 30;

  Future<List<ConversationSession>> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_sessionsKey);
      if (raw == null || raw.isEmpty) return [];
      final list = (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
      return list.map(ConversationSession.fromJson).toList();
    } catch (_) {
      return [];
    }
  }

  Future<String?> loadCurrentId() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(_currentKey);
    } catch (_) {
      return null;
    }
  }

  Future<void> save(List<ConversationSession> sessions, String? currentId) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
        _sessionsKey,
        jsonEncode(sessions.take(maxSessions).map((s) => s.toJson()).toList()),
      );
      await prefs.setString(_currentKey, currentId ?? '');
    } catch (_) {}
  }
}
