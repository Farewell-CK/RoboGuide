import 'package:flutter/material.dart';

/// Hold-to-talk button.
class PttButton extends StatefulWidget {
  final bool enabled;
  final bool talking;
  final VoidCallback onPressStart;
  final VoidCallback onPressEnd;
  const PttButton({
    super.key,
    required this.enabled,
    required this.talking,
    required this.onPressStart,
    required this.onPressEnd,
  });

  @override
  State<PttButton> createState() => _PttButtonState();
}

class _PttButtonState extends State<PttButton> {
  bool _pressed = false;

  void _handleDown() {
    if (!widget.enabled) return;
    setState(() => _pressed = true);
    widget.onPressStart();
  }

  void _handleUp() {
    if (!_pressed) return;
    setState(() => _pressed = false);
    widget.onPressEnd();
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.talking || _pressed;
    final color = !widget.enabled
        ? Colors.grey
        : active
            ? Colors.redAccent
            : Colors.teal;
    return Listener(
      onPointerDown: (_) => _handleDown(),
      onPointerUp: (_) => _handleUp(),
      onPointerCancel: (_) => _handleUp(),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 120),
        height: 72,
        decoration: BoxDecoration(
          color: color.withValues(alpha: active ? 0.9 : 0.15),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: color, width: 2),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(active ? Icons.mic : Icons.mic_none, size: 28, color: color),
            const SizedBox(width: 8),
            Text(
              !widget.enabled
                  ? 'Connect first'
                  : active
                      ? 'Speaking...'
                      : 'Hold to Talk',
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
