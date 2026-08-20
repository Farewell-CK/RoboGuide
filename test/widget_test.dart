import 'package:flutter_test/flutter_test.dart';

import 'package:roboguide_remote/main.dart';

void main() {
  testWidgets('App renders home page', (WidgetTester tester) async {
    // Build our app and trigger a frame.
    await tester.pumpWidget(const RoboguideRemoteApp());

    // Home page shows the connect button.
    expect(find.text('Connect'), findsOneWidget);
    expect(find.text('Roboguide Remote'), findsOneWidget);
  });
}
