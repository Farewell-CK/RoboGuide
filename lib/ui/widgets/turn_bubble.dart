import 'package:flutter/material.dart';

import '../../session/session_store.dart';

/// One conversation turn rendered as user/assistant bubbles.
class TurnBubble extends StatelessWidget {
  final ConversationTurn turn;
  final VoidCallback onDelete;
  const TurnBubble({super.key, required this.turn, required this.onDelete});

  String get _time {
    final t = turn.startedAt;
    return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (turn.userText.isNotEmpty)
            Align(
              alignment: Alignment.centerRight,
              child: Container(
                constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.78),
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(turn.userText),
              ),
            ),
          if (turn.assistantText.isNotEmpty)
            Align(
              alignment: Alignment.centerLeft,
              child: Container(
                margin: const EdgeInsets.only(top: 2),
                constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.78),
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(turn.assistantText),
              ),
            ),
          if (turn.userText.isEmpty &&
              turn.assistantText.isEmpty &&
              turn.error.isEmpty)
            const Padding(
              padding: EdgeInsets.only(bottom: 4),
              child: Text('正在等待语音结果…',
                  style: TextStyle(fontSize: 12, color: Colors.grey)),
            ),
          if (turn.error.isNotEmpty)
            Text(
              turn.error,
              style: TextStyle(color: theme.colorScheme.error, fontSize: 12),
            ),
          Row(
            mainAxisAlignment: turn.userText.isNotEmpty
                ? MainAxisAlignment.end
                : MainAxisAlignment.start,
            children: [
              Text(_time,
                  style: theme.textTheme.labelSmall
                      ?.copyWith(color: Colors.grey)),
              const SizedBox(width: 6),
              Chip(
                label: Text(turn.state,
                    style: const TextStyle(fontSize: 10)),
                visualDensity: VisualDensity.compact,
                padding: EdgeInsets.zero,
              ),
              IconButton(
                onPressed: onDelete,
                icon: const Icon(Icons.delete_outline, size: 16),
                tooltip: '删除此回合',
                visualDensity: VisualDensity.compact,
                padding: EdgeInsets.zero,
                constraints:
                    const BoxConstraints(minWidth: 28, minHeight: 28),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
