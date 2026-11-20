import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:path_provider/path_provider.dart';

/// Local, account-scoped traces. No credentials, embeddings or photos are saved.
class NavigationDiagnostics {
  static const retainedSessions = 3;
  static const retainedEvents = 180;
  Future<void> _pending = Future.value();
  Map<String, dynamic>? _record;
  String? _userId;

  static Future<Directory> _root(String userId) async {
    final documents = await getApplicationDocumentsDirectory();
    final safeId = userId.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');
    return Directory('${documents.path}/navigation_diagnostics/$safeId');
  }

  void start(String? userId, Map<String, dynamic> route) {
    _userId = userId;
    _record = {
      'id': DateTime.now().microsecondsSinceEpoch.toString(),
      'started': DateTime.now().toIso8601String(),
      'route': route,
      'events': <Map<String, dynamic>>[],
    };
    add({'type': 'start'});
  }

  void add(Map<String, dynamic> event) {
    final record = _record;
    final userId = _userId;
    if (record == null || userId == null) return;
    final events = record['events'] as List<Map<String, dynamic>>;
    events.add({'time': DateTime.now().toIso8601String(), ...event});
    if (events.length > retainedEvents) events.removeAt(0);
    // Capture a snapshot before queuing so a restart cannot overwrite this session.
    final json = jsonEncode(record);
    final id = record['id'] as String;
    _pending = _pending
        .then((_) async {
          final root = await _root(userId);
          await root.create(recursive: true);
          final temp = File('${root.path}/$id.tmp');
          await temp.writeAsString(json, flush: true);
          await temp.rename('${root.path}/$id.json');
          final files =
              await root.list().where((e) => e.path.endsWith('.json')).toList();
          files.sort((a, b) => b.path.compareTo(a.path));
          for (final old in files.skip(retainedSessions)) {
            await old.delete();
          }
        })
        .catchError((Object error) {
          // Diagnostics failures must never interrupt navigation.
          print('导航诊断保存失败：$error');
        });
  }

  Future<void> flush() => _pending;

  static Future<List<Map<String, dynamic>>> read(String userId) async {
    final root = await _root(userId);
    if (!await root.exists()) return [];
    final files =
        await root.list().where((e) => e.path.endsWith('.json')).toList();
    files.sort((a, b) => b.path.compareTo(a.path));
    final records = <Map<String, dynamic>>[];
    for (final file in files.take(retainedSessions)) {
      try {
        records.add(
          Map<String, dynamic>.from(
            jsonDecode(await File(file.path).readAsString()) as Map,
          ),
        );
      } catch (_) {}
    }
    return records;
  }

  static Future<void> clear(String userId) async {
    final root = await _root(userId);
    if (await root.exists()) await root.delete(recursive: true);
  }
}
