import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import '../services/registration_http_diagnostics.dart';
import '../services/supabase_service.dart';
import 'registration_feedback.dart';

class RegisterScreen extends StatefulWidget {
  final bool allowAdminCreation;

  const RegisterScreen({Key? key, this.allowAdminCreation = false})
    : super(key: key);

  @override
  _RegisterScreenState createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();
  final _adminCodeController = TextEditingController();
  final _supabaseService = SupabaseService();
  bool _isLoading = false;
  String? _errorMessage;
  String? _registrationDiagnostic;
  bool _isAdmin = false;
  bool _showAdminCodeField = false;

  static const String _adminRegistrationCode = "ADMIN123";

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    _adminCodeController.dispose();
    super.dispose();
  }

  Future<void> _signUp() async {
    if (!_formKey.currentState!.validate()) return;
    if (_isAdmin && _adminCodeController.text != _adminRegistrationCode) {
      setState(() {
        _errorMessage = '管理员注册码不正确，请核对后重试或注册普通用户。';
        _registrationDiagnostic = null;
      });
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
      _registrationDiagnostic = null;
      RegistrationRequestObservation.last = null;
    });

    try {
      // Let Auth check duplicates; the deployed project has no email_exists RPC.
      final result = await _supabaseService.signUpWithCheck(
        email: _emailController.text.trim(),
        password: _passwordController.text,
        role: _isAdmin ? UserRole.admin : UserRole.user,
      );
      if (!mounted) return;

      if (result.authResponse.user != null && result.isNewUser) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              result.authResponse.session != null
                  ? '注册成功，已自动登录。'
                  : '注册成功！请查收邮件并确认账号。',
            ),
            backgroundColor: Colors.green,
          ),
        );
        Navigator.of(context).pop();
      } else {
        setState(() {
          _errorMessage = '该邮箱可能已注册，请尝试登录或找回密码。';
        });
      }
    } catch (error, stack) {
      if (!mounted) return;
      setState(() {
        _errorMessage = registrationErrorMessage(error);
        _registrationDiagnostic = registrationDiagnostic(
          error,
          stack,
          configuredUrl: dotenv.env['SUPABASE_URL'],
          configuredKey: dotenv.env['SUPABASE_ANON_KEY'],
        );
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final mediaQuery = MediaQuery.of(context);
    final screenWidth = mediaQuery.size.width;
    final screenHeight = mediaQuery.size.height;
    final keyboardHeight = mediaQuery.viewInsets.bottom;
    final isKeyboardVisible = keyboardHeight > 0;

    final bool isSmallPhone = screenHeight < 600;
    final double iconSize = isKeyboardVisible ? 50.0 : 70.0;
    final double titleFontSize = screenWidth < 360 ? 20.0 : 22.0;
    final double subtitleFontSize = isSmallPhone ? 12.0 : 14.0;
    final double horizontalPadding = screenWidth * 0.05;
    final double verticalSpacing = isKeyboardVisible ? 10.0 : 16.0;

    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        title: Semantics(label: 'Register screen', child: const Text('注册')),
        elevation: 0,
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
      ),
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            return SingleChildScrollView(
              padding: EdgeInsets.symmetric(
                horizontal: horizontalPadding.clamp(16.0, 24.0),
                vertical: 16.0,
              ),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (!isKeyboardVisible) ...[
                      Semantics(
                        label: 'Registration icon',
                        child: Icon(
                          Icons.person_add,
                          size: iconSize,
                          color: Colors.black,
                        ),
                      ),
                      SizedBox(height: verticalSpacing),
                      if (_registrationDiagnostic != null) ...[
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(12),
                          color: Colors.grey.shade100,
                          child: SelectableText(
                            _registrationDiagnostic!,
                            style: const TextStyle(
                              fontSize: 12,
                              color: Colors.black87,
                            ),
                          ),
                        ),
                        SizedBox(height: verticalSpacing),
                      ],
                    ],
                    Semantics(
                      label: 'Registration title',
                      child: Text(
                        '创建账号',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: titleFontSize,
                          fontWeight: FontWeight.bold,
                          color: Colors.black,
                        ),
                      ),
                    ),
                    SizedBox(height: verticalSpacing * 1.5),
                    if (_errorMessage != null) ...[
                      Semantics(
                        label: 'Error message',
                        child: Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: Colors.red.shade50,
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(
                              color: Colors.red.shade600,
                              width: 2,
                            ),
                          ),
                          child: Row(
                            children: [
                              Semantics(
                                label: 'Error icon',
                                child: Icon(
                                  Icons.error_outline,
                                  color: Colors.red.shade600,
                                  size: 24,
                                ),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Text(
                                  _errorMessage!,
                                  style: TextStyle(
                                    color: Colors.red.shade800,
                                    fontSize: 14,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                      SizedBox(height: verticalSpacing),
                    ],
                    Semantics(
                      label: 'Email input field',
                      hint: 'Enter your email address',
                      textField: true,
                      child: TextFormField(
                        controller: _emailController,
                        style: const TextStyle(
                          fontSize: 16,
                          color: Colors.black,
                        ),
                        decoration: InputDecoration(
                          labelText: '邮箱',
                          labelStyle: const TextStyle(color: Colors.black87),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black,
                              width: 2,
                            ),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black54,
                              width: 1,
                            ),
                          ),
                          focusedBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black,
                              width: 2,
                            ),
                          ),
                          filled: true,
                          fillColor: Colors.white,
                          prefixIcon: const Icon(
                            Icons.email,
                            color: Colors.black87,
                          ),
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 16,
                          ),
                        ),
                        keyboardType: TextInputType.emailAddress,
                        validator: (value) {
                          if (value == null || value.isEmpty) {
                            return '请输入邮箱';
                          }
                          if (!value.contains('@') || !value.contains('.')) {
                            return '请输入有效的邮箱地址';
                          }
                          return null;
                        },
                      ),
                    ),
                    SizedBox(height: verticalSpacing),
                    Semantics(
                      label: 'Password input field',
                      hint: 'Enter your password',
                      textField: true,
                      child: TextFormField(
                        controller: _passwordController,
                        style: const TextStyle(
                          fontSize: 16,
                          color: Colors.black,
                        ),
                        decoration: InputDecoration(
                          labelText: '密码',
                          labelStyle: const TextStyle(color: Colors.black87),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black,
                              width: 2,
                            ),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black54,
                              width: 1,
                            ),
                          ),
                          focusedBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black,
                              width: 2,
                            ),
                          ),
                          filled: true,
                          fillColor: Colors.white,
                          prefixIcon: const Icon(
                            Icons.lock,
                            color: Colors.black87,
                          ),
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 16,
                          ),
                        ),
                        obscureText: true,
                        validator: (value) {
                          if (value == null || value.isEmpty) {
                            return 'Please enter a password';
                          }
                          if (value.length < 6) {
                            return 'Password should be at least 6 characters';
                          }
                          return null;
                        },
                      ),
                    ),
                    SizedBox(height: verticalSpacing),
                    Semantics(
                      label: 'Confirm password input field',
                      hint: 'Re-enter your password to confirm',
                      textField: true,
                      child: TextFormField(
                        controller: _confirmPasswordController,
                        style: const TextStyle(
                          fontSize: 16,
                          color: Colors.black,
                        ),
                        decoration: InputDecoration(
                          labelText: '确认密码',
                          labelStyle: const TextStyle(color: Colors.black87),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black,
                              width: 2,
                            ),
                          ),
                          enabledBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black54,
                              width: 1,
                            ),
                          ),
                          focusedBorder: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(12),
                            borderSide: const BorderSide(
                              color: Colors.black,
                              width: 2,
                            ),
                          ),
                          filled: true,
                          fillColor: Colors.white,
                          prefixIcon: const Icon(
                            Icons.lock_outline,
                            color: Colors.black87,
                          ),
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 16,
                            vertical: 16,
                          ),
                        ),
                        obscureText: true,
                        validator: (value) {
                          if (value == null || value.isEmpty) {
                            return '请再次输入密码';
                          }
                          if (value != _passwordController.text) {
                            return '两次输入的密码不一致';
                          }
                          return null;
                        },
                      ),
                    ),
                    SizedBox(height: verticalSpacing),

                    // Only show this if admin creation is allowed
                    widget.allowAdminCreation
                        ? Card(
                          child: SwitchListTile(
                            title: const Text('管理员注册'),
                            subtitle: const Text('需要管理员注册码'),
                            value: _isAdmin,
                            onChanged: (value) {
                              setState(() {
                                _isAdmin = value;
                                _showAdminCodeField = value;
                              });
                            },
                          ),
                        )
                        : const SizedBox.shrink(),

                    // Admin code field - only shown when admin option selected
                    _showAdminCodeField
                        ? Column(
                          children: [
                            SizedBox(height: verticalSpacing),
                            Semantics(
                              label: 'Admin code input field',
                              hint: 'Enter the admin registration code',
                              textField: true,
                              child: TextFormField(
                                controller: _adminCodeController,
                                style: const TextStyle(
                                  fontSize: 16,
                                  color: Colors.black,
                                ),
                                decoration: InputDecoration(
                                  labelText: '管理员注册码',
                                  labelStyle: const TextStyle(
                                    color: Colors.black87,
                                  ),
                                  border: OutlineInputBorder(
                                    borderRadius: BorderRadius.circular(12),
                                    borderSide: const BorderSide(
                                      color: Colors.black,
                                      width: 2,
                                    ),
                                  ),
                                  enabledBorder: OutlineInputBorder(
                                    borderRadius: BorderRadius.circular(12),
                                    borderSide: const BorderSide(
                                      color: Colors.black54,
                                      width: 1,
                                    ),
                                  ),
                                  focusedBorder: OutlineInputBorder(
                                    borderRadius: BorderRadius.circular(12),
                                    borderSide: const BorderSide(
                                      color: Colors.black,
                                      width: 2,
                                    ),
                                  ),
                                  filled: true,
                                  fillColor: Colors.white,
                                  prefixIcon: const Icon(
                                    Icons.admin_panel_settings,
                                    color: Colors.black87,
                                  ),
                                  contentPadding: const EdgeInsets.symmetric(
                                    horizontal: 16,
                                    vertical: 16,
                                  ),
                                ),
                                obscureText: true,
                                validator: (value) {
                                  if (_isAdmin) {
                                    if (value == null || value.isEmpty) {
                                      return 'Admin code is required';
                                    }
                                  }
                                  return null;
                                },
                              ),
                            ),
                          ],
                        )
                        : const SizedBox.shrink(),

                    SizedBox(height: verticalSpacing * 2),
                    Semantics(
                      label: 'Register button',
                      hint: 'Tap to create your account',
                      button: true,
                      child: ElevatedButton(
                        onPressed: _isLoading ? null : _signUp,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.green.shade600,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(vertical: 16),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12),
                          ),
                          elevation: 2,
                        ),
                        child:
                            _isLoading
                                ? const SizedBox(
                                  height: 20,
                                  width: 20,
                                  child: CircularProgressIndicator(
                                    color: Colors.white,
                                    strokeWidth: 2,
                                  ),
                                )
                                : const Text(
                                  '注册',
                                  style: TextStyle(
                                    fontSize: 16,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                      ),
                    ),
                    SizedBox(height: verticalSpacing),
                    Semantics(
                      label: 'Login link',
                      hint: 'Tap to go back to login screen',
                      button: true,
                      child: TextButton(
                        onPressed: () {
                          Navigator.of(context).pop();
                        },
                        child: const Text(
                          '已有账号？登录',
                          style: TextStyle(fontSize: 14, color: Colors.black87),
                        ),
                      ),
                    ),
                    SizedBox(height: mediaQuery.padding.bottom + 16),
                  ],
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}
