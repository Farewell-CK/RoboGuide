import 'package:flutter/material.dart';

class LocalizationReadyPanel extends StatelessWidget {
  const LocalizationReadyPanel({
    super.key,
    required this.status,
    required this.verifyWithGpt,
    required this.peopleInpainting,
    required this.onVerificationChanged,
    required this.onPeopleChanged,
  });

  final String status;
  final bool verifyWithGpt;
  final bool peopleInpainting;
  final ValueChanged<bool>? onVerificationChanged;
  final ValueChanged<bool> onPeopleChanged;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: Colors.black.withValues(alpha: 0.75),
      borderRadius: BorderRadius.circular(12),
    ),
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(Icons.location_searching, color: Colors.blue, size: 32),
        const SizedBox(height: 8),
        const Text(
          '准备定位',
          textAlign: TextAlign.center,
          style: TextStyle(
            color: Colors.white,
            fontSize: 20,
            fontWeight: FontWeight.bold,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          status,
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.white70, fontSize: 13),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            'GPT 辅助验证',
            style: TextStyle(color: Colors.white70, fontSize: 14),
          ),
          value: verifyWithGpt,
          onChanged: onVerificationChanged,
        ),
        if (onVerificationChanged == null)
          const Text(
            '未配置 GPT 验证，可直接使用 DINOv2 定位',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.orange, fontSize: 12),
          ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text(
            '行人消除',
            style: TextStyle(color: Colors.white70, fontSize: 14),
          ),
          subtitle: const Text(
            '当前演示请保持关闭',
            style: TextStyle(color: Colors.white60, fontSize: 12),
          ),
          value: peopleInpainting,
          onChanged: onPeopleChanged,
        ),
      ],
    ),
  );
}
