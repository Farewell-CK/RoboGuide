import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../services/obstacle_monitor.dart';

class ObstacleAssistPanel extends StatelessWidget {
  const ObstacleAssistPanel({super.key, required this.monitor});
  final ObstacleMonitor monitor;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: monitor,
    builder:
        (context, _) => Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          decoration: BoxDecoration(
            color: const Color(0xEE182635),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      '室内避障（实验）',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  IconButton(
                    tooltip: '识别诊断',
                    icon: const Icon(Icons.info_outline, color: Colors.white70),
                    onPressed: () => _showDiagnostics(context),
                  ),
                  Semantics(
                    label: '视觉避障开关',
                    child: Switch(
                      value: monitor.enabled,
                      onChanged: monitor.setEnabled,
                    ),
                  ),
                ],
              ),
              ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 80),
                child: SingleChildScrollView(
                  child: Semantics(
                    liveRegion: monitor.warning,
                    child: Text(
                      monitor.status,
                      style: TextStyle(
                        color: monitor.warning ? Colors.amber : Colors.white70,
                        fontSize: 14,
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 4),
            ],
          ),
        ),
  );

  void _showDiagnostics(BuildContext context) {
    final rows = monitor.rawPredictions
        .map((p) {
          final disposition = switch (p['disposition']) {
            'candidate' => '进入候选',
            'filtered_scene' => '室内场景已过滤',
            _ => '分数或面积不足',
          };
          return p['model_label'].toString() +
              '：分数 ' +
              ((p['confidence'] as num) * 100).toStringAsFixed(0) +
              '%，画面占比 ' +
              ((p['occupancy'] as num) * 100).toStringAsFixed(1) +
              '%；' +
              disposition;
        })
        .join('\n');
    showDialog<void>(
      context: context,
      builder:
          (dialogContext) => AlertDialog(
            title: const Text('室内避障诊断'),
            content: SingleChildScrollView(
              child: SelectableText(
                '模型：Swin-L / ADE20K\n确认：' +
                    monitor.confirmationStatus +
                    '\n服务处理：' +
                    monitor.inferenceMs.toString() +
                    ' ms\n\n' +
                    (rows.isEmpty ? '暂无本帧分类记录。' : rows) +
                    '\n\n分数是模型内部得分，不代表识别正确率。可复制最近30帧记录；不保存照片。',
              ),
            ),
            actions: [
              TextButton(
                onPressed: () async {
                  await Clipboard.setData(
                    ClipboardData(text: monitor.exportDiagnostics()),
                  );
                  if (dialogContext.mounted) Navigator.pop(dialogContext);
                },
                child: const Text('复制诊断记录'),
              ),
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('关闭'),
              ),
            ],
          ),
    );
  }
}
