import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:indoor_navigation_app/auth/registration_feedback.dart';
import 'package:indoor_navigation_app/services/registration_http_diagnostics.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    RegistrationRequestObservation.last = null;
  });

  test('HTTP response is preserved and diagnostics omit secrets', () async {
    const key = 'sb_publishable_PRIVATEKEY';
    const password = 'PRIVATEPASSWORD';
    const body = '{"message":"Invalid API key","private":"PRIVATERESPONSE"}';
    final client = RegistrationDiagnosticClient(
      MockClient((request) async {
        expect(request.body, '{"password":"PRIVATEPASSWORD"}');
        return http.Response(body, 401);
      }),
    );
    final response = await client.post(
      Uri.parse(
        'http://backend.invalid:18001/auth/v1/signup?redirect_to=PRIVATEREDIRECT',
      ),
      headers: {'apikey': key},
      body: '{"password":"$password"}',
    );
    expect(response.statusCode, 401);
    expect(response.body, body);
    final diagnostic = registrationDiagnostic(
      const AuthException(
        'PRIVATERESPONSE',
        statusCode: '401',
        code: 'invalid_api_key',
      ),
      StackTrace.empty,
    );
    expect(diagnostic, contains('HTTP：401'));
    expect(diagnostic, contains('错误代码：invalid_api_key'));
    expect(diagnostic, contains('阶段：已收到服务器响应'));
    expect(
      diagnostic,
      contains('地址：http://backend.invalid:18001/auth/v1/signup'),
    );
    for (final secret in [
      key,
      password,
      'PRIVATERESPONSE',
      'PRIVATEREDIRECT',
    ]) {
      expect(diagnostic, isNot(contains(secret)));
    }
    client.close();
  });

  test('transport failure is distinct from rejection by the server', () async {
    final client = RegistrationDiagnosticClient(
      MockClient((request) async {
        throw http.ClientException('PRIVATE_RESPONSE_CONTENT');
      }),
    );
    Object? failure;
    try {
      await client.post(
        Uri.parse('http://backend.invalid:18001/auth/v1/signup'),
      );
    } catch (error) {
      failure = error;
    }
    final diagnostic = registrationDiagnostic(failure!, StackTrace.empty);
    expect(diagnostic, contains('HTTP：未收到'));
    expect(diagnostic, contains('阶段：请求发送或传输失败'));
    expect(diagnostic, contains('ClientException'));
    expect(diagnostic, isNot(contains('PRIVATE_RESPONSE_CONTENT')));
    client.close();
  });

  test('failure before HTTP includes the stage without exception arguments', () {
    final diagnostic = registrationDiagnostic(
      ArgumentError('PRIVATEPASSWORD'),
      StackTrace.fromString(
        '#0 auth (package:gotrue/src/gotrue_client.dart:1:1)',
      ),
      configuredUrl:
          'http://user:PRIVATE_URL_PASSWORD@backend.invalid:18001/?token=PRIVATEQUERY',
      configuredKey: 'sb_publishable_PRIVATEKEY',
    );
    expect(diagnostic, contains('未观察到注册 HTTP 请求'));
    expect(diagnostic, contains('ArgumentError'));
    expect(diagnostic, contains('gotrue_client.dart'));
    for (final value in [
      'PRIVATEPASSWORD',
      'PRIVATE_URL_PASSWORD',
      'PRIVATEQUERY',
      'sb_publishable_PRIVATEKEY',
    ]) {
      expect(diagnostic, isNot(contains(value)));
    }
  });

  test(
    'default Flutter PKCE startup can dispatch signup and expose an error code',
    () async {
      SharedPreferences.setMockInitialValues({});
      var dispatched = false;
      await Supabase.initialize(
        url: 'http://backend.invalid:18001',
        publishableKey: 'sb_publishable_OFFLINE_TEST_ONLY',
        debug: false,
        httpClient: RegistrationDiagnosticClient(
          MockClient((request) async {
            expect(request.url.path, '/auth/v1/signup');
            expect(jsonDecode(request.body)['code_challenge'], isNotEmpty);
            dispatched = true;
            return http.Response(
              jsonEncode({
                'error_code': 'invalid_api_key',
                'msg': 'Invalid API key',
              }),
              401,
            );
          }),
        ),
        authOptions: const FlutterAuthClientOptions(
          autoRefreshToken: false,
          persistSession: false,
          detectSessionInUri: false,
        ),
      );
      try {
        Object? failure;
        try {
          await Supabase.instance.client.auth.signUp(
            email: 'offline@example.test',
            password: 'OfflinePassword123!',
          );
        } catch (error) {
          failure = error;
        }
        expect(dispatched, isTrue);
        expect(failure, isA<AuthException>());
        expect(
          registrationDiagnostic(failure!, StackTrace.empty),
          contains('HTTP：401'),
        );
        expect(registrationErrorMessage(failure), contains('服务器未接受公开客户端密钥'));
      } finally {
        await Supabase.instance.dispose();
      }
    },
  );
}
