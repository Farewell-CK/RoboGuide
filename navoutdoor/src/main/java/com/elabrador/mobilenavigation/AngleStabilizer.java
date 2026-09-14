package com.elabrador.mobilenavigation;

/**
 * Stabilizes a noisy signed angle (target bearing or pursuit steering) with a short
 * circular median window plus hysteresis: the published value only shifts when the
 * window medoid has moved at least the configured degrees away from it. A walking
 * straight-line segment then keeps a stable direction instead of swinging with
 * per-frame cost-grid jitter.
 */
final class AngleStabilizer {
    private final float[] window;
    private final float shiftThresholdDegrees;
    private int size;
    private float stableDegrees = Float.NaN;

    AngleStabilizer(int windowSize, float shiftThresholdDegrees) {
        if (windowSize < 1) throw new IllegalArgumentException("windowSize must be positive");
        window = new float[windowSize];
        this.shiftThresholdDegrees = shiftThresholdDegrees;
    }

    /**
     * Pushes one raw sample and returns the stabilized angle. Non-finite raw samples
     * keep the last stable value (or pass through when nothing stable exists yet).
     */
    synchronized float update(float rawDegrees) {
        if (!Float.isFinite(rawDegrees)) {
            return Float.isFinite(stableDegrees) ? stableDegrees : rawDegrees;
        }
        window[size % window.length] = rawDegrees;
        size++;
        float median = circularMedoid();
        if (!Float.isFinite(stableDegrees)) {
            stableDegrees = median;
        } else if (Math.abs(DynamicHeadingCalibrator.normalizeDegrees(
                median - stableDegrees)) >= shiftThresholdDegrees) {
            stableDegrees = median;
        }
        return stableDegrees;
    }

    synchronized void reset() {
        size = 0;
        stableDegrees = Float.NaN;
    }

    /** Circular medoid: the sample with the smallest worst-case distance to the rest. */
    private float circularMedoid() {
        int count = Math.min(size, window.length);
        float best = window[0];
        float bestScore = Float.POSITIVE_INFINITY;
        for (int i = 0; i < count; i++) {
            float worst = 0f;
            for (int j = 0; j < count; j++) {
                float distance = (float) Math.abs(DynamicHeadingCalibrator.normalizeDegrees(
                        window[i] - window[j]));
                if (distance > worst) worst = distance;
            }
            if (worst < bestScore) {
                bestScore = worst;
                best = window[i];
            }
        }
        return best;
    }
}
