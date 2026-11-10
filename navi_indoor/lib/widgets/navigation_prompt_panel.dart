import 'package:flutter/material.dart';
import '../services/navigation_prompts.dart';

/// Keep the current guidance and actions visible while details can scroll.
class NavigationPromptLayout extends StatelessWidget {
  const NavigationPromptLayout({
    super.key,
    required this.prompts,
    required this.details,
    required this.actions,
  });

  final NavigationPrompts prompts;
  final Widget details;
  final Widget actions;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(12),
    color: Colors.black.withValues(alpha: .15),
    child: ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * .70,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Flexible(child: SingleChildScrollView(child: details)),
          const SizedBox(height: 8),
          NavigationPromptPanel(prompts: prompts),
          const SizedBox(height: 8),
          actions,
        ],
      ),
    ),
  );
}

class NavigationPromptPanel extends StatelessWidget {
  const NavigationPromptPanel({super.key, required this.prompts});
  final NavigationPrompts prompts;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: prompts,
    builder: (context, _) {
      final warning = prompts.kind == NavigationPromptKind.warning;
      final arrived = prompts.kind == NavigationPromptKind.arrival;
      return Semantics(
        container: true,
        liveRegion: true,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
          decoration: BoxDecoration(
            color: arrived ? const Color(0xF01B5E20) : const Color(0xF0182635),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: warning ? Colors.amber : Colors.white54),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              ConstrainedBox(
                constraints: BoxConstraints(
                  maxHeight: (MediaQuery.sizeOf(context).height * .18).clamp(
                    64,
                    140,
                  ),
                ),
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(
                        prompts.text,
                        style: TextStyle(
                          color: warning ? Colors.amber : Colors.white,
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      if (prompts.voiceError != null)
                        Text(
                          prompts.voiceError!,
                          style: const TextStyle(
                            color: Colors.amber,
                            fontSize: 12,
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              Wrap(
                alignment: WrapAlignment.end,
                spacing: 8,
                children: [
                  TextButton.icon(
                    onPressed: prompts.toggleVoice,
                    icon: Icon(
                      prompts.voiceEnabled ? Icons.volume_up : Icons.volume_off,
                      size: 20,
                    ),
                    label: Text(prompts.voiceEnabled ? '语音已开启' : '语音已关闭'),
                    style: TextButton.styleFrom(foregroundColor: Colors.white),
                  ),
                  TextButton.icon(
                    onPressed: prompts.voiceEnabled ? prompts.repeat : null,
                    icon: const Icon(Icons.replay, size: 20),
                    label: const Text('重播提示'),
                    style: TextButton.styleFrom(
                      foregroundColor: Colors.white,
                      disabledForegroundColor: Colors.white38,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      );
    },
  );
}
