import 'package:flutter/material.dart';

/// Straight references can merge; this is tracking progress, not distance.
class NavigationProgressPanel extends StatelessWidget {
  const NavigationProgressPanel({
    super.key,
    required this.completed,
    required this.total,
    required this.status,
    required this.instruction,
    this.motionHint,
    this.onConfirmMovement,
  });
  final int completed;
  final int total;
  final String status;
  final String instruction;
  final String? motionHint;
  final VoidCallback? onConfirmMovement;

  @override
  Widget build(BuildContext context) {
    final confirmed = completed.clamp(0, total);
    final percent = total == 0 ? 0 : (100 * confirmed / total).round();
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: .8),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white30),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            instruction,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            status,
            style: const TextStyle(color: Colors.white, fontSize: 14),
          ),
          const SizedBox(height: 6),
          Text(
            '路线跟踪 $confirmed / $total · $percent%',
            style: const TextStyle(color: Colors.white70, fontSize: 14),
          ),
          if (confirmed < total)
            Text(
              '下一参考点：第 ${confirmed + 1} 个',
              style: const TextStyle(color: Colors.white70, fontSize: 14),
            ),
          const SizedBox(height: 6),
          LinearProgressIndicator(
            value: total == 0 ? 0 : confirmed / total,
            backgroundColor: Colors.grey,
            valueColor: const AlwaysStoppedAnimation<Color>(Colors.white),
          ),
          const SizedBox(height: 6),
          const Text(
            '直行参考点可能合并跟踪，转弯和终点需单独确认。进度不是行走距离；请沿已知路线慢行，方向不符时停下核对。',
            style: TextStyle(color: Colors.white70, fontSize: 12),
          ),
          if (motionHint != null)
            Text(
              motionHint!,
              style: const TextStyle(color: Colors.amber, fontSize: 12),
            ),
          if (onConfirmMovement != null)
            TextButton.icon(
              onPressed: onConfirmMovement,
              icon: const Icon(Icons.directions_walk, color: Colors.white),
              label: const Text(
                '确认已到达此处，重新识别',
                style: TextStyle(color: Colors.white),
              ),
            ),
        ],
      ),
    );
  }
}
