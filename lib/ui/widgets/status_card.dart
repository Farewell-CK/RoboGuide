import 'package:flutter/material.dart';

/// Connection + audio health card.
class StatusCard extends StatelessWidget {
  final bool connected;
  final bool connecting;
  final String mode; // spp | ws
  final bool micActive;
  final String audioState;
  final int sentBytes;
  final int receivedBytes;
  final int reconnects;
  final String linkQuality;
  const StatusCard({
    super.key,
    required this.connected,
    required this.connecting,
    required this.mode,
    required this.micActive,
    required this.audioState,
    required this.sentBytes,
    required this.receivedBytes,
    required this.reconnects,
    required this.linkQuality,
  });

  @override
  Widget build(BuildContext context) {
    final color = connected
        ? Colors.green
        : connecting
            ? Colors.orange
            : Colors.grey;
    final label = connected ? 'Connected' : connecting ? 'Connecting' : 'Disconnected';
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(Icons.circle, size: 12, color: color),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  'Link: $label (${mode.toUpperCase()})',
                  style: Theme.of(context).textTheme.titleMedium,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (micActive)
                const Chip(
                  label: Text('MIC ACTIVE'),
                  backgroundColor: Colors.redAccent,
                  labelStyle: TextStyle(color: Colors.white, fontSize: 11),
                  visualDensity: VisualDensity.compact,
                ),
            ]),
            const SizedBox(height: 2),
            Text(
              '$audioState · 质量 $linkQuality · 重连 $reconnects · '
              'TX ${sentBytes}B / RX ${receivedBytes}B',
              style: const TextStyle(fontSize: 11),
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ),
      ),
    );
  }
}
