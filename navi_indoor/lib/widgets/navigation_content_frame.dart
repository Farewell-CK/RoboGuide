import 'package:flutter/material.dart';

/// Bounds both painting and hit testing, including when Android enlarges text.
class NavigationContentFrame extends StatelessWidget {
  const NavigationContentFrame({
    super.key,
    required this.child,
    this.orientation = false,
  });

  final Widget child;
  final bool orientation;

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final height = (media.size.height * (orientation ? 0.28 : 0.48)).clamp(
      140.0,
      460.0,
    );
    return SizedBox(
      height: height,
      child: SingleChildScrollView(
        child: ConstrainedBox(
          constraints: BoxConstraints(minHeight: height),
          child: child,
        ),
      ),
    );
  }
}
