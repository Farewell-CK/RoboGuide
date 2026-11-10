import 'package:flutter/material.dart';

/// Arrival stays visible until explicitly dismissed, even without audio/network.
class NavigationArrivalPanel extends StatelessWidget {
  const NavigationArrivalPanel({
    super.key,
    required this.destination,
    required this.total,
  });
  final String destination;
  final int total;

  @override
  Widget build(BuildContext context) => Semantics(
    liveRegion: true,
    child: Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xEE1B5E20),
        borderRadius: BorderRadius.circular(16),
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.check_circle, color: Colors.white, size: 44),
            const SizedBox(height: 8),
            Text(
              '已到达$destination',
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 24,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              '路线已完成 · $total / $total · 100%',
              style: const TextStyle(color: Colors.white, fontSize: 16),
            ),
            const SizedBox(height: 8),
            const Text(
              '本次导航结束，请停下。',
              style: TextStyle(color: Colors.white, fontSize: 16),
            ),
          ],
        ),
      ),
    ),
  );
}
