import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../models/embedding_values.dart';
import '../models/localization_policy.dart';
import '../models/navigation_match_policy.dart';
import '../services/localization_diagnostics.dart';
import '../services/navigation_diagnostics.dart';
import '../services/supabase_service.dart';

class LocalizationDiagnosticsScreen extends StatefulWidget {
  const LocalizationDiagnosticsScreen({super.key});

  @override
  State<LocalizationDiagnosticsScreen> createState() => _DiagnosticsState();
}

class _DiagnosticsState extends State<LocalizationDiagnosticsScreen> {
  final _client = SupabaseService().client;
  List<Map<String, dynamic>> _history = [];
  List<Map<String, dynamic>> _navigationHistory = [];
  List<Map<String, dynamic>> _maps = [];
  List<Map<String, dynamic>> _nodes = [];
  List<Map<String, dynamic>> _references = [];
  String? _error;
  String? _mapId;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final userId = _client.auth.currentUser?.id;
      if (userId == null) throw Exception('请先登录');
      // Read local results first; a network error must not hide them.
      final history = await LocalizationDiagnostics.read(userId);
      final navigationHistory = await NavigationDiagnostics.read(userId);
      if (!mounted) return;
      setState(() {
        _history = history;
        _navigationHistory = navigationHistory;
      });
      final maps = await _client
          .from('maps')
          .select('id,name,image_url')
          .timeout(const Duration(seconds: 25));
      final nodes = await _client
          .from('map_nodes')
          .select('id,name,map_id')
          .timeout(const Duration(seconds: 25));
      // Page explicitly: PostgREST's default row limit must not hide references.
      final references = <Map<String, dynamic>>[];
      for (var offset = 0; ; offset += 500) {
        final page = await _client
            .from('place_embeddings')
            .select('id,node_id,place_name,embedding')
            .order('id')
            .range(offset, offset + 499)
            .timeout(const Duration(seconds: 25));
        for (final row in page) {
          var valid = false;
          try {
            valid = isUsableEmbedding(parseNumericValues(row['embedding']));
          } catch (_) {}
          references.add({
            'id': row['id'],
            'node_id': row['node_id'],
            'name': row['place_name'],
            'valid': valid,
          });
        }
        if (page.length < 500) break;
      }
      if (!mounted) return;
      setState(() {
        _maps = List<Map<String, dynamic>>.from(maps);
        _nodes = List<Map<String, dynamic>>.from(nodes);
        _references = references;
        if (!_maps.any((m) => m['id'] == _mapId))
          _mapId = _maps.isEmpty ? null : _maps.first['id'] as String;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '读取云端数据失败：$e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Widget _reference(String embeddingId) =>
      _ReferenceImage(key: ValueKey(embeddingId), embeddingId: embeddingId);

  Widget _navigationAttempt(Map<String, dynamic> record) {
    final route = Map<String, dynamic>.from(record['route'] as Map? ?? {});
    final events = List<Map<String, dynamic>>.from(
      record['events'] as List? ?? [],
    );
    final points = List<Map<String, dynamic>>.from(
      route['waypoints'] as List? ?? [],
    );
    return Card(
      child: ExpansionTile(
        title: Text('${record['started']}\n${route['name']}'),
        subtitle: Text(
          '${events.where((e) => e['type'] == 'frame').length} 次画面比对；只保留最近 180 条事件',
        ),
        children: [
          TextButton.icon(
            icon: const Icon(Icons.copy),
            label: const Text('复制这次导航记录'),
            onPressed: () async {
              await Clipboard.setData(
                ClipboardData(
                  text: const JsonEncoder.withIndent('  ').convert(record),
                ),
              );
              if (mounted)
                ScaffoldMessenger.of(
                  context,
                ).showSnackBar(const SnackBar(content: Text('已复制，可粘贴发送用于排查')));
            },
          ),
          ExpansionTile(
            title: const Text('录制路线的采样点与朝向'),
            children: [
              for (final point in points)
                ListTile(
                  dense: true,
                  title: Text(
                    '第 ${(point['sequence'] as num).toInt() + 1} 个点 · ${point['turn']} · ${point['heading']}°',
                  ),
                  subtitle: Text('特征维度 ${point['dimensions']}'),
                ),
            ],
          ),
          for (final event in events.reversed)
            ListTile(
              dense: true,
              title: Text(
                event['type'] == 'frame'
                    ? '寻找第 ${(event['target'] as num).toInt() + 1} 个点 · ${event['matched'] == true ? '确认通过' : '未推进'}\n相似度 ${(event['score'] as num).toStringAsFixed(3)} / 门槛 ${event['threshold']}\n${event['decision_message'] ?? ''}'
                    : '${event['type']} · ${event['state'] ?? event['message'] ?? ''}',
              ),
              subtitle: Text(
                '${event['time']}\n${event['type'] == 'frame' ? '处理 ${event['elapsed_ms']} ms；步行 ${event['steps'] ?? '旧版未记录'}，检测 ${event['motion_available'] ?? '未知'}\n参考窗口末点 ${event['forward_limit']}；候选点 ${event['candidate_sequence']}\n当前朝向 ${event['heading']}°，录制 ${event['reference_heading']}°\n最相似的路线点：${(event['candidates'] as List? ?? []).map((c) => '第 ${(c['sequence'] as num).toInt() + 1} 点 ${(c['score'] as num).toStringAsFixed(3)}').join('；')}' : ''}',
              ),
            ),
        ],
      ),
    );
  }

  Widget _attempt(Map<String, dynamic> attempt) {
    final frames = List<Map<String, dynamic>>.from(
      attempt['frames'] as List? ?? [],
    );
    final images = List<String>.from(attempt['images'] as List? ?? []);
    final scores =
        frames
            .expand(
              (f) => List<Map<String, dynamic>>.from(
                f['candidates'] as List? ?? [],
              ),
            )
            .map((c) => (c['score'] as num).toDouble())
            .toList()
          ..sort((a, b) => b.compareTo(a));
    final best =
        scores.isEmpty ? '未完成比对' : '最高相似度 ${scores.first.toStringAsFixed(4)}';
    return Card(
      child: ExpansionTile(
        title: Text('${attempt['time']}\n$best'),
        subtitle: Text(
          '${attempt['outcome']}\n门槛：严格大于 ${attempt['threshold']}；有效参考特征 ${attempt['reference_count'] ?? '未知'} 条',
        ),
        children: [
          if (attempt['captured_frame_count'] == 0)
            const ListTile(title: Text('本次未获得可比对的图片，请先处理上方的相机或连接提示。')),
          for (final frame in frames)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('第 ${(frame['index'] as num).toInt() + 1} 张画面'),
                  if ((frame['index'] as int) < images.length)
                    Image.file(
                      File(images[frame['index'] as int]),
                      height: 220,
                      fit: BoxFit.contain,
                      errorBuilder: (_, __, ___) => const Text('本地画面已不可用'),
                    ),
                  for (final candidate in List<Map<String, dynamic>>.from(
                    frame['candidates'] as List? ?? [],
                  ))
                    ExpansionTile(
                      title: Text(
                        '${candidate['name']}：${(candidate['score'] as num).toStringAsFixed(4)}',
                      ),
                      subtitle: const Text('展开查看对应的已上传参考图'),
                      children: [
                        _reference(candidate['embedding_id'] as String),
                      ],
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _mapContent() {
    if (_maps.isEmpty) return const Text('当前账号未读取到地图。请先检查云端连接。');
    final map = _maps.firstWhere(
      (m) => m['id'] == _mapId,
      orElse: () => _maps.first,
    );
    final dataUrl = map['image_url'] as String? ?? '';
    Uint8List? mapBytes;
    if (dataUrl.startsWith('data:')) {
      try {
        mapBytes = base64Decode(dataUrl.split(',').last);
      } catch (_) {}
    }
    final nodes = _nodes.where((n) => n['map_id'] == _mapId).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DropdownButtonFormField<String>(
          value: _mapId,
          isExpanded: true,
          items:
              _maps
                  .map(
                    (m) => DropdownMenuItem(
                      value: m['id'] as String,
                      child: Text(m['name'] as String),
                    ),
                  )
                  .toList(),
          onChanged: (id) => setState(() => _mapId = id),
        ),
        if (mapBytes != null)
          Image.memory(mapBytes, height: 220, fit: BoxFit.contain),
        Text('此地图读取到 ${nodes.length} 个节点。展开节点查看参考图与特征有效性。'),
        for (final node in nodes) _node(node),
      ],
    );
  }

  Widget _node(Map<String, dynamic> node) {
    final refs = _references.where((r) => r['node_id'] == node['id']).toList();
    final valid = refs.where((r) => r['valid'] == true).length;
    return Card(
      child: ExpansionTile(
        title: Text(node['name'] as String? ?? '未命名节点'),
        subtitle: Text(
          '参考特征 ${refs.length} 条：有效 $valid，失效 ${refs.length - valid}\n图片打不开时会显示具体错误。',
        ),
        children: [
          for (var i = 0; i < refs.length; i++)
            ExpansionTile(
              title: Text(
                '参考图 ${i + 1} · ${refs[i]['valid'] == true ? '特征有效' : '特征失效，需重录'}',
              ),
              children: [_reference(refs[i]['id'] as String)],
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('定位诊断'),
      actions: [
        IconButton(
          onPressed: _loading ? null : _load,
          icon: const Icon(Icons.refresh),
          tooltip: '刷新云端数据',
        ),
      ],
    ),
    body: ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          '当前 DINOv2 定位门槛：严格大于 $localizationSimilarityThreshold。本机保存最近 3 次画面和分数，不上传定位照片；历史记录保留当时使用的门槛。',
        ),
        const Text(
          '行走时普通采样点门槛：$navigationWaypointThreshold；转弯前、转弯点和终点：$navigationCriticalWaypointThreshold。',
        ),
        const Text(
          '直行段在当前及后两个普通参考点内连续 $navigationConfirmationFrames 帧确认，不要求领先其他点；需新步行信号（不可用时手动确认移动）。转弯前、转弯点和终点不跳过。进度不代表实际距离。',
        ),
        TextButton(
          onPressed: () async {
            final id = _client.auth.currentUser?.id;
            if (id != null) {
              await LocalizationDiagnostics.clear(id);
              await NavigationDiagnostics.clear(id);
            }
            if (mounted)
              setState(() {
                _history = [];
                _navigationHistory = [];
              });
          },
          child: const Text('清除本机诊断记录'),
        ),
        const Text(
          '最近导航比对记录',
          style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
        ),
        const Text(
          '本机保留最近 3 次导航，每次最多 180 条事件。记录目标点、实际分数、门槛、最相似的路线点和处理耗时；不把画面匹配视为位移证明。',
        ),
        if (_navigationHistory.isEmpty) const Text('安装此版本后导航一次，再停止导航并回到这里查看。'),
        ..._navigationHistory.map(_navigationAttempt),
        const SizedBox(height: 20),
        const Text(
          '最近定位记录',
          style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
        ),
        if (_history.isEmpty) const Text('暂无记录。安装此版本后重新定位一次，再回到这里查看。'),
        ..._history.map(_attempt),
        const SizedBox(height: 20),
        const Text(
          '云端地图与节点参考图',
          style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
        ),
        if (_loading) const LinearProgressIndicator(),
        if (_error != null)
          SelectableText(_error!, style: const TextStyle(color: Colors.red)),
        if (!_loading) _mapContent(),
      ],
    ),
  );
}

class _ReferenceImage extends StatefulWidget {
  final String embeddingId;
  const _ReferenceImage({super.key, required this.embeddingId});
  @override
  State<_ReferenceImage> createState() => _ReferenceImageState();
}

class _ReferenceImageState extends State<_ReferenceImage> {
  late final Future<Uint8List> _image = SupabaseService().client.storage
      .from('reference-images')
      .download('${widget.embeddingId}_reference.jpg')
      .timeout(const Duration(seconds: 25));
  @override
  Widget build(BuildContext context) => FutureBuilder<Uint8List>(
    future: _image,
    builder: (_, snapshot) {
      if (snapshot.hasError)
        return Padding(
          padding: const EdgeInsets.all(12),
          child: SelectableText('参考图读取失败（可能缺图或网络/权限问题）：${snapshot.error}'),
        );
      if (!snapshot.hasData)
        return const Padding(
          padding: EdgeInsets.all(20),
          child: CircularProgressIndicator(),
        );
      return Image.memory(
        snapshot.data!,
        height: 260,
        fit: BoxFit.contain,
        errorBuilder: (_, __, ___) => const Text('参考图片无法解码'),
      );
    },
  );
}
