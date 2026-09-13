package com.elabrador.mobilenavigation;

final class NavigationCue {
    private NavigationCue() {}

    static String fromPlan(boolean planned, boolean success, boolean blocked, float steering) {
        if (!planned) return "";
        if (!success || blocked) return "停止";
        if (!Float.isFinite(steering)) return "";
        float magnitude = Math.abs(steering);
        if (magnitude <= 10f) return "直走";
        if (magnitude >= 135f) return "向后转";
        return steering > 0f ? "左转" : "右转";
    }
}
