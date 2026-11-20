package com.elabrador.mobilenavigation

/** Called by the video loop while video is still arriving. Motion timestamps are callback arrival times. */
internal class D455ImuWatchdog {
    private var startedAt = 0L
    @Volatile private var lastGyroAt = -1L
    @Volatile private var lastAccelAt = -1L
    private var healthySince = -1L
    private var restartedSinceHealthy = false

    fun onStreamStart(now: Long) {
        startedAt = now
        lastGyroAt = -1L
        lastAccelAt = -1L
        healthySince = -1L
    }

    fun onGyro(now: Long) {
        lastGyroAt = now
    }

    fun onAccel(now: Long) {
        lastAccelAt = now
    }

    fun shouldRestart(now: Long): Boolean {
        val oldestMotionAt = minOf(lastGyroAt, lastAccelAt)
        if (oldestMotionAt >= 0 && now - oldestMotionAt < 1000L) {
            if (healthySince < 0) healthySince = now
            if (now - healthySince >= 5000L) restartedSinceHealthy = false
            return false
        }
        healthySince = -1L
        if (restartedSinceHealthy || now - maxOf(startedAt, oldestMotionAt) < 5000L) return false
        restartedSinceHealthy = true
        return true
    }
}
