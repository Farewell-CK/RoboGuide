import 'package:http/http.dart' as http;
import 'package:supabase_flutter/supabase_flutter.dart';

/// In-memory metadata only: never retain request bodies or credential values.
class RegistrationRequestObservation {
  RegistrationRequestObservation(http.BaseRequest request)
    : address =
          request.url.replace(userInfo: '', query: '', fragment: '').toString(),
      started = DateTime.now(),
      keyLength = (request.headers['apikey'] ?? '').length,
      keyType = registrationKeyType(request.headers['apikey'] ?? '');
  final String address;
  final DateTime started;
  final int keyLength;
  final String keyType;
  int? status;
  String? transportErrorType;
  static RegistrationRequestObservation? last;
}

String registrationKeyType(String key) =>
    key.startsWith('sb_publishable_')
        ? 'publishable'
        : key.startsWith('eyJ')
        ? 'anon JWT'
        : key.isEmpty
        ? '未填写'
        : '其他格式';

class RegistrationDiagnosticClient extends http.BaseClient {
  RegistrationDiagnosticClient([http.Client? inner])
    : _inner = inner ?? http.Client();
  final http.Client _inner;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final observed = request.url.path == '/auth/v1/signup';
    final trace = observed ? RegistrationRequestObservation(request) : null;
    if (trace != null) RegistrationRequestObservation.last = trace;
    try {
      final response = await _inner.send(request);
      if (trace != null) trace.status = response.statusCode;
      return response;
    } catch (error) {
      if (trace != null)
        trace.transportErrorType = error.runtimeType.toString();
      rethrow;
    }
  }

  @override
  void close() => _inner.close();
}

String registrationDiagnostic(
  Object error,
  StackTrace stack, {
  String? configuredUrl,
  String? configuredKey,
}) {
  final trace = RegistrationRequestObservation.last;
  final auth = error is AuthException ? error : null;
  final safeCode =
      auth?.code != null &&
              RegExp(r'^[a-zA-Z0-9_]{1,64}$').hasMatch(auth!.code!)
          ? auth.code!
          : '无';
  final uri = Uri.tryParse(configuredUrl ?? '');
  final address =
      trace?.address ??
      uri?.replace(userInfo: '', query: '', fragment: '').toString() ??
      '未知';
  final key = configuredKey?.trim() ?? '';
  final frames = stack
      .toString()
      .split('\n')
      .where(
        (line) =>
            line.contains('gotrue') ||
            line.contains('supabase_service.dart') ||
            line.contains('supabase_flutter') ||
            line.contains('http/'),
      )
      .take(2);
  final safeFrames = frames.map(
    (line) => line.length > 190 ? line.substring(0, 190) : line,
  );
  return [
    '注册诊断 · NAVI 1.0.17',
    '时间：${DateTime.now().toIso8601String()}',
    '异常：${error.runtimeType}',
    '错误代码：$safeCode',
    'HTTP：${auth?.statusCode ?? trace?.status ?? "未收到"}',
    '阶段：${trace == null
        ? "未观察到注册 HTTP 请求"
        : trace.status == null
        ? "请求发送或传输失败"
        : "已收到服务器响应"}',
    '地址：$address',
    '密钥：${trace?.keyType ?? registrationKeyType(key)} / ${trace?.keyLength ?? key.length} 字符',
    if (trace?.transportErrorType != null) '传输异常：${trace!.transportErrorType}',
    if (safeFrames.isNotEmpty) '位置：${safeFrames.join(" | ")}',
  ].join('\n');
}
