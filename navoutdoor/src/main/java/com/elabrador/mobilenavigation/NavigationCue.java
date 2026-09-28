package com.elabrador.mobilenavigation;

final class NavigationCue {
    private NavigationCue() {}

    static String fromPlan(boolean planned, boolean success, boolean blocked, float steering) {
        if (!planned) return "";
        if (!success || blocked) return "停止";
        if (!Float.isFinite(steering)) return "";
        float magnitude = Math.abs(steering);
        // Demonstration profile: tolerate small pursuit-angle noise on straight roads.
        // Demonstration profile: keep walking straight through turns up to 30°.
        if (magnitude < 30f) return "直走";
        if (magnitude >= 135f) return "向后转";
        return steering > 0f ? "左转" : "右转";
    }

    /**
     * Stateful variant that reports the turn magnitude in 10° steps, so a human does
     * not interpret every cue as a 90° turn, and applies hysteresis between adjacent
     * steps of the same direction to keep the text from flickering with pursuit-angle
     * noise. Category changes (straight / either side / u-turn) take effect at once.
     */
    static final class Tracker {
        private static final int STEP_DEGREES = 10;
        private static final int HOLD_DEGREES = 20;
        private Integer lastQuantized;

        String update(boolean planned, boolean success, boolean blocked, float steering) {
            if (!planned) return "";
            if (!success || blocked) {
                lastQuantized = null;
                return "停止";
            }
            if (!Float.isFinite(steering)) return "";
            int quantized = quantize(steering);
            if (lastQuantized != null
                    && category(lastQuantized) == category(quantized)
                    && Math.abs(quantized - lastQuantized) < HOLD_DEGREES) {
                quantized = lastQuantized;
            }
            lastQuantized = quantized;
            return cueFor(quantized);
        }

        void reset() {
            lastQuantized = null;
        }

        /** Signed 10°-quantized magnitude; 0 is straight, ±180 is a u-turn. */
        private static int quantize(float steering) {
            float rawMagnitude = Math.abs(steering);
            // Keep cues straight below 30°; exactly 30° is a turn cue.
            if (rawMagnitude < 30f) return 0;
            int magnitude = Math.round(rawMagnitude / STEP_DEGREES) * STEP_DEGREES;
            if (magnitude < STEP_DEGREES) magnitude = STEP_DEGREES;
            int sign = steering > 0f ? 1 : -1;
            if (magnitude >= 135) return sign * 180;
            return sign * magnitude;
        }

        /** 0 straight, 1 left turn, -1 right turn, 2 u-turn. */
        private static int category(int quantized) {
            int magnitude = Math.abs(quantized);
            if (magnitude == 0) return 0;
            if (magnitude >= 180) return 2;
            return Integer.signum(quantized);
        }

        private static String cueFor(int quantized) {
            if (quantized == 0) return "直走";
            int magnitude = Math.abs(quantized);
            if (magnitude >= 180) return "向后转";
            String side = quantized > 0 ? "左转" : "右转";
            return side + " " + magnitude + "°";
        }
    }
}
