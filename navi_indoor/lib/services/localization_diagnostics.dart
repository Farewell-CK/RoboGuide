import 'dart:convert';
import 'dart:io';
import 'package:path_provider/path_provider.dart';

/// Bounded, account-specific diagnostics stored on this phone only.
class LocalizationDiagnostics {
  static const retainedAttempts = 3;

  static Future<Directory> _root(String userId) async {
    final documents = await getApplicationDocumentsDirectory();
    final safeId = userId.replaceAll(RegExp(r'[^a-zA-Z0-9_-]'), '_');
    return Directory('${documents.path}/localization_diagnostics/$safeId');
  }

  static Future<List<Map<String, dynamic>>> read(String userId) async {
    final root = await _root(userId);
    if (!await root.exists()) return [];
    final directories = await root.list().where((e) => e is Directory).toList();
    directories.sort((a, b) => b.path.compareTo(a.path));
    final records = <Map<String, dynamic>>[];
    for (final directory in directories) {
      try {
        records.add(
          Map<String, dynamic>.from(
            jsonDecode(
                  await File('${directory.path}/record.json').readAsString(),
                )
                as Map,
          ),
        );
      } catch (_) {
        // An interrupted write must not hide other completed attempts.
      }
    }
    return records;
  }

  static Future<void> save({
    required String userId,
    required Map<String, dynamic> record,
    required List<File> images,
  }) async {
    final root = await _root(userId);
    final directory = Directory(
      '${root.path}/${DateTime.now().microsecondsSinceEpoch}',
    );
    await directory.create(recursive: true);
    final paths = <String>[];
    for (var i = 0; i < images.length; i++) {
      final target = '${directory.path}/frame_$i.jpg';
      await images[i].copy(target);
      paths.add(target);
    }
    await File(
      '${directory.path}/record.json',
    ).writeAsString(jsonEncode({...record, 'images': paths}), flush: true);
    final directories = await root.list().where((e) => e is Directory).toList();
    directories.sort((a, b) => b.path.compareTo(a.path));
    for (final old in directories.skip(retainedAttempts)) {
      await old.delete(recursive: true);
    }
  }

  static Future<void> clear(String userId) async {
    final root = await _root(userId);
    if (await root.exists()) await root.delete(recursive: true);
  }
}
