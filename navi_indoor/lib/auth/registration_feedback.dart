import 'package:supabase_flutter/supabase_flutter.dart';

/// Keep actionable Auth errors visible without exposing raw server responses.
String registrationErrorMessage(Object error) {
  final code = error is AuthException ? error.code : null;
  final message = error.toString().toLowerCase();
  if (message.contains('invalid api key') || message.contains('no api key')) {
    return '服务器未接受公开客户端密钥，请核对配置；下方诊断信息可用于定位。';
  }
  if (error is AuthException && error.statusCode == '401') {
    return '注册请求未通过认证（HTTP 401），请查看下方诊断信息。';
  }
  if (message.contains('database error') ||
      message.contains('saving new user')) {
    return '服务器创建账号时发生数据库错误，请联系管理员检查注册触发器。';
  }
  if (code == 'email_address_not_authorized' ||
      message.contains('email address not authorized')) {
    return '注册邮件发送受限，请联系管理员关闭邮箱确认或配置邮件服务。';
  }
  if (code == 'user_already_exists' ||
      message.contains('already registered') ||
      message.contains('already exists')) {
    return '该邮箱已注册，请直接登录或找回密码。';
  }
  if (code == 'email_address_invalid' || message.contains('invalid email')) {
    return '邮箱格式不正确，请检查后重试。';
  }
  if (code == 'weak_password' || message.contains('password')) {
    return '密码不符合要求，请使用更强的密码。';
  }
  if (code == 'over_email_send_rate_limit' ||
      code == 'over_request_rate_limit' ||
      message.contains('rate limit')) {
    return '注册请求过于频繁，请稍后重试。';
  }
  if (code == 'signup_disabled') {
    return '服务器暂未开放注册，请联系管理员。';
  }
  if (message.contains('network') ||
      message.contains('connection') ||
      message.contains('socketexception') ||
      message.contains('timeout')) {
    return '网络连接失败，请检查网络和服务器地址后重试。';
  }
  return '注册失败，请稍后重试；若持续失败，请联系管理员查看后台记录。';
}
