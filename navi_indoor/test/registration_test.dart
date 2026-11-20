import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:indoor_navigation_app/auth/registration_feedback.dart';
import 'package:indoor_navigation_app/services/supabase_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final user = <String, dynamic>{
    'id': '11111111-1111-4111-8111-111111111111',
    'aud': 'authenticated',
    'email': 'offline-user@example.test',
    'created_at': '2026-11-13T00:00:00Z',
    'app_metadata': {'provider': 'email'},
    'user_metadata': {'role': 'user'},
    'identities': [
      {
        'id': '11111111-1111-4111-8111-111111111111',
        'user_id': '11111111-1111-4111-8111-111111111111',
        'provider': 'email',
        'identity_data': {'email': 'offline-user@example.test'},
      },
    ],
  };
  late Map<String, dynamic> body;
  late int status;
  late List<String> requests;
  String encoded(Object value) =>
      base64UrlEncode(utf8.encode(jsonEncode(value))).replaceAll('=', '');

  setUp(() async {
    body = user;
    status = 200;
    requests = [];
    SharedPreferences.setMockInitialValues({});
    await Supabase.initialize(
      url: 'https://navi-auth-diagnostic.invalid',
      publishableKey: 'sb_publishable_OFFLINE_TEST_ONLY',
      httpClient: MockClient((request) async {
        requests.add(request.url.path);
        expect(request.url.path, '/auth/v1/signup');
        return http.Response(
          jsonEncode(body),
          status,
          headers: {'content-type': 'application/json'},
        );
      }),
      debug: false,
      authOptions: const FlutterAuthClientOptions(
        authFlowType: AuthFlowType.implicit,
        autoRefreshToken: false,
        persistSession: false,
        detectSessionInUri: false,
      ),
    );
  });
  tearDown(() async => Supabase.instance.dispose());

  Future<SignUpResult> signUp() => SupabaseService().signUpWithCheck(
    email: 'offline-user@example.test',
    password: 'OfflineTestPassword123!',
    role: UserRole.user,
  );

  test('new account with an automatic login session succeeds', () async {
    final token = [
      encoded({'alg': 'HS256', 'typ': 'JWT'}),
      encoded({
        'sub': user['id'],
        'exp':
            DateTime.now()
                .add(const Duration(hours: 1))
                .millisecondsSinceEpoch ~/
            1000,
      }),
      encoded('offline-signature'),
    ].join('.');
    body = {
      'access_token': token,
      'token_type': 'bearer',
      'expires_in': 3600,
      'refresh_token': 'OFFLINE_TEST_ONLY',
      'user': user,
    };
    final result = await signUp();
    expect(result.isNewUser, isTrue);
    expect(result.authResponse.session, isNotNull);
    expect(requests, ['/auth/v1/signup']);
  });

  test('new account awaiting confirmation still succeeds', () async {
    final result = await signUp();
    expect(result.isNewUser, isTrue);
    expect(result.authResponse.session, isNull);
    expect(requests, ['/auth/v1/signup']);
  });

  test(
    'obfuscated duplicate account is not accepted as a new account',
    () async {
      body = {...user, 'identities': []};
      final result = await signUp();
      expect(result.isNewUser, isFalse);
      expect(result.authResponse.session, isNull);
    },
  );

  test(
    'existing account error is preserved and explained in Chinese',
    () async {
      status = 422;
      body = {
        'code': 422,
        'error_code': 'user_already_exists',
        'msg': 'User already registered',
      };
      await expectLater(
        signUp(),
        throwsA(
          isA<AuthException>().having(
            (e) => registrationErrorMessage(e),
            'message',
            '该邮箱已注册，请直接登录或找回密码。',
          ),
        ),
      );
    },
  );

  test(
    'restricted email delivery has an actionable Chinese explanation',
    () async {
      status = 403;
      body = {
        'code': 403,
        'error_code': 'email_address_not_authorized',
        'msg': 'Email address not authorized',
      };
      await expectLater(
        signUp(),
        throwsA(
          isA<AuthException>().having(
            (e) => registrationErrorMessage(e),
            'message',
            contains('注册邮件发送受限'),
          ),
        ),
      );
    },
  );

  test(
    'rate limits are distinct from weak passwords and connection errors',
    () {
      expect(
        registrationErrorMessage(
          const AuthException(
            'Email rate limit exceeded',
            code: 'over_email_send_rate_limit',
          ),
        ),
        '注册请求过于频繁，请稍后重试。',
      );
      expect(
        registrationErrorMessage(
          const AuthException('Password is too short', code: 'weak_password'),
        ),
        '密码不符合要求，请使用更强的密码。',
      );
      expect(
        registrationErrorMessage(
          Exception('SocketException: connection refused'),
        ),
        '网络连接失败，请检查网络和服务器地址后重试。',
      );
      expect(
        registrationErrorMessage(const AuthException('Unknown error')),
        contains('联系管理员查看后台记录'),
      );
    },
  );
}
