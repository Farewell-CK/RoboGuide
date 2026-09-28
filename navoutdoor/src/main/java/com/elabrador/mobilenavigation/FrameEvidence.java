package com.elabrador.mobilenavigation;

/** Immutable acquisition identity; reprojection must retain this object. */
final class FrameEvidence {
    static final long MAX_AGE_MILLIS = 1500;
    final double cameraSeconds;
    final long generation;
    final long captureElapsedMillis;
    final long submittedElapsedMillis;

    FrameEvidence(double cameraSeconds, long generation, long nowWallMillis, long nowElapsedMillis) {
        this.cameraSeconds=cameraSeconds;
        this.generation=generation;
        submittedElapsedMillis=nowElapsedMillis;
        double age=nowWallMillis-cameraSeconds*1000.0;
        captureElapsedMillis=Double.isFinite(age) && age>=-100 && age<60000
                ? nowElapsedMillis-(long)Math.max(0,age) : -1;
    }
    FrameEvidence(double cameraSeconds, long generation, long captureElapsedMillis) {
        this.cameraSeconds=cameraSeconds; this.generation=generation;
        this.captureElapsedMillis=captureElapsedMillis; this.submittedElapsedMillis=captureElapsedMillis;
    }
    long ageMillis(long now) { return captureElapsedMillis<0 ? Long.MAX_VALUE : now-captureElapsedMillis; }
    boolean fresh(long now) { long age=ageMillis(now); return age>=0 && age<MAX_AGE_MILLIS; }
}
