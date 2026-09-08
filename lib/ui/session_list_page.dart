import 'package:flutter/material.dart';

import '../session/session_store.dart';

/// Session list management page.
class SessionListPage extends StatelessWidget {
  final List<ConversationSession> sessions;
  final String? currentId;
  final ValueChanged<ConversationSession> onSelect;
  final VoidCallback onNewSession;
  final Future<void> Function(ConversationSession) onRename;
  final Future<void> Function(ConversationSession) onDelete;

  const SessionListPage({
    super.key,
    required this.sessions,
    required this.currentId,
    required this.onSelect,
    required this.onNewSession,
    required this.onRename,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('会话列表'),
        actions: [
          IconButton(
            onPressed: onNewSession,
            icon: const Icon(Icons.add_circle_outline),
            tooltip: '新建会话',
          ),
        ],
      ),
      body: sessions.isEmpty
          ? const Center(child: Text('暂无会话，点击右上角 ➕ 新建'))
          : ListView.builder(
              itemCount: sessions.length,
              itemBuilder: (context, index) {
                final s = sessions[index];
                final last = s.turns.isEmpty
                    ? ''
                    : s.turns.first.assistantText.isNotEmpty
                        ? s.turns.first.assistantText
                        : s.turns.first.userText;
                final selected = s.id == currentId;
                final time =
                    '${s.lastActiveAt.month}/${s.lastActiveAt.day} '
                    '${s.lastActiveAt.hour.toString().padLeft(2, '0')}:'
                    '${s.lastActiveAt.minute.toString().padLeft(2, '0')}';
                return Card(
                  child: ListTile(
                    onTap: () => onSelect(s),
                    onLongPress: () => onRename(s),
                    leading: CircleAvatar(
                      backgroundColor: selected
                          ? Theme.of(context).colorScheme.primary
                          : null,
                      child: Icon(
                        Icons.chat_bubble_outline,
                        size: 20,
                        color: selected
                            ? Theme.of(context).colorScheme.onPrimary
                            : null,
                      ),
                    ),
                    title: Row(
                      children: [
                        Expanded(
                          child: Text(
                            s.title,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontWeight: selected
                                  ? FontWeight.bold
                                  : FontWeight.normal,
                            ),
                          ),
                        ),
                        if (selected)
                          const Padding(
                            padding: EdgeInsets.only(left: 6),
                            child: Text('当前',
                                style: TextStyle(
                                    fontSize: 10, color: Colors.teal)),
                          ),
                      ],
                    ),
                    subtitle: Text(
                      s.turns.isEmpty
                          ? '$time · 空会话'
                          : '$time · ${s.turns.length} 回合 · ${last.length > 24 ? last.substring(0, 24) : last}',
                      overflow: TextOverflow.ellipsis,
                      maxLines: 1,
                    ),
                    trailing: IconButton(
                      onPressed: () => onDelete(s),
                      icon: const Icon(Icons.delete_outline),
                      tooltip: '删除会话（长按可重命名）',
                    ),
                  ),
                );
              },
            ),
    );
  }
}
