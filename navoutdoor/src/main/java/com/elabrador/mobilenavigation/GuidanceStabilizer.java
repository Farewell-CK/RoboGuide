package com.elabrador.mobilenavigation;

/** Coalesces rapid direction changes; stop and navigation end bypass the hold. */
final class GuidanceStabilizer {
    private String displayed = "";
    private long changedAt;

    String update(String next, boolean active, long nowMillis) {
        if (!active || next == null || next.isEmpty()) {
            reset();
            return displayed;
        }
        if (next.equals(displayed)) return displayed;
        if (displayed.isEmpty() || "停止".equals(next) || nowMillis - changedAt >= 1000L) {
            displayed = next;
            changedAt = nowMillis;
        }
        return displayed;
    }

    void reset() {
        displayed = "";
        changedAt = 0L;
    }
}
