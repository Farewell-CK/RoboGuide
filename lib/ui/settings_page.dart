import 'package:flutter/material.dart';

import '../../config/app_config.dart';
import '../../transport/robot_transport.dart';

/// Settings: transport backend, network debug endpoints, reconnection.
class SettingsPage extends StatefulWidget {
  final AppSettings settings;
  final VoidCallback onChanged;
  const SettingsPage({super.key, required this.settings, required this.onChanged});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late AppSettings _s;

  @override
  void initState() {
    super.initState();
    _s = widget.settings;
  }

  Future<void> _save() async {
    await _s.save();
    widget.onChanged();
    if (mounted) setState(() {});
  }

  Future<void> _editField({
    required String title,
    required String label,
    required String initial,
    required ValueChanged<String> onApply,
    TextInputType keyboard = TextInputType.text,
  }) async {
    final controller = TextEditingController(text: initial);
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: controller,
          autofocus: true,
          keyboardType: keyboard,
          decoration: InputDecoration(labelText: label),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text),
            child: const Text('确定'),
          ),
        ],
      ),
    );
    if (value != null && value.trim().isNotEmpty) {
      onApply(value.trim());
      await _save();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: ListView(
        children: [
          const SizedBox(height: 8),
          _SectionHeader('连接方式'),
          RadioGroup<TransportMode>(
            groupValue: _s.mode,
            onChanged: (m) {
              setState(() => _s.mode = m ?? TransportMode.spp);
              _save();
            },
            child: const Column(
              children: [
                RadioListTile<TransportMode>(
                  title: Text('蓝牙 SPP(默认)'),
                  subtitle: Text('经典蓝牙 RFCOMM,贴近机器人时使用'),
                  value: TransportMode.spp,
                ),
                RadioListTile<TransportMode>(
                  title: Text('网络调试(WS + gRPC)'),
                  subtitle: Text('Tailscale/LAN 远程调试:音频走桥 :60002,语音事件走 Liaison :50081'),
                  value: TransportMode.ws,
                ),
              ],
            ),
          ),
          const Divider(),
          _SectionHeader('网络调试端点'),
          ListTile(
            title: const Text('机器人地址'),
            subtitle: Text(_s.wsHost),
            trailing: const Icon(Icons.edit_outlined),
            onTap: () => _editField(
              title: '机器人地址',
              label: 'IP / Tailscale 地址',
              initial: _s.wsHost,
              onApply: (v) => _s.wsHost = v,
            ),
          ),
          ListTile(
            title: const Text('音频桥端口'),
            subtitle: Text('${_s.wsPort}'),
            trailing: const Icon(Icons.edit_outlined),
            onTap: () => _editField(
              title: '音频桥端口',
              label: '端口',
              initial: '${_s.wsPort}',
              keyboard: TextInputType.number,
              onApply: (v) {
                final p = int.tryParse(v);
                if (p != null && p > 0) _s.wsPort = p;
              },
            ),
          ),
          ListTile(
            title: const Text('Liaison gRPC 端口'),
            subtitle: Text('${_s.liaisonPort}'),
            trailing: const Icon(Icons.edit_outlined),
            onTap: () => _editField(
              title: 'Liaison gRPC 端口',
              label: '端口',
              initial: '${_s.liaisonPort}',
              keyboard: TextInputType.number,
              onApply: (v) {
                final p = int.tryParse(v);
                if (p != null && p > 0) _s.liaisonPort = p;
              },
            ),
          ),
          const Divider(),
          _SectionHeader('蓝牙'),
          ListTile(
            title: const Text('默认设备 MAC'),
            subtitle: Text(_s.mac),
            trailing: const Icon(Icons.edit_outlined),
            onTap: () => _editField(
              title: '默认设备 MAC',
              label: '蓝牙 MAC 地址',
              initial: _s.mac,
              onApply: (v) => _s.mac = v,
            ),
          ),
          const Divider(),
          SwitchListTile(
            title: const Text('自动重连'),
            subtitle: const Text('断开后按指数退避自动恢复(蓝牙模式)'),
            value: _s.autoReconnect,
            onChanged: (v) {
              setState(() => _s.autoReconnect = v);
              _save();
            },
          ),
        ],
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String text;
  const _SectionHeader(this.text);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Text(
        text,
        style: Theme.of(context).textTheme.labelLarge?.copyWith(
              color: Theme.of(context).colorScheme.primary,
            ),
      ),
    );
  }
}
