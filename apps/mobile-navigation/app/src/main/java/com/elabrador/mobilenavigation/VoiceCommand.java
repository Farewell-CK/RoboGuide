package com.elabrador.mobilenavigation;

final class VoiceCommand {
    enum Type { DESTINATION, START, PAUSE, RESUME, END, UNKNOWN }

    final Type type;
    final String value;

    private VoiceCommand(Type type, String value) {
        this.type = type;
        this.value = value;
    }

    static VoiceCommand parse(String recognized) {
        String text = recognized == null ? "" : recognized.trim()
                .replaceAll("[，。！？、,.!?\\s]+", "");
        if (text.contains("开始导航")) return new VoiceCommand(Type.START, "");
        if (text.contains("继续导航") || text.contains("恢复导航")) {
            return new VoiceCommand(Type.RESUME, "");
        }
        if (text.contains("结束导航") || text.contains("停止导航")
                || text.contains("取消导航")) {
            return new VoiceCommand(Type.END, "");
        }
        if (text.contains("暂停导航")) {
            return new VoiceCommand(Type.PAUSE, "");
        }
        String[] prefixes = {"请带我导航到", "带我导航到", "我要导航到", "导航到",
                "导航去", "目的地是", "我要去", "前往", "去"};
        for (String prefix : prefixes) {
            int position = text.indexOf(prefix);
            if (position >= 0) {
                String destination = text.substring(position + prefix.length());
                if (!destination.isEmpty()) {
                    return new VoiceCommand(Type.DESTINATION, destination);
                }
            }
        }
        // When voice control is explicitly enabled, an address/name by itself is useful.
        if (text.length() >= 2) return new VoiceCommand(Type.DESTINATION, text);
        return new VoiceCommand(Type.UNKNOWN, "");
    }
}
