package com.elabrador.mobilenavigation;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class D455ImuWatchdogTest {
    @Test
    public void restartsOnlyOnceUntilBothMotionStreamsHaveRecovered() {
        D455ImuWatchdog watchdog = new D455ImuWatchdog();
        watchdog.onStreamStart(0);
        watchdog.onGyro(100);
        watchdog.onAccel(100);
        watchdog.onGyro(5000);
        assertFalse(watchdog.shouldRestart(5099));
        assertTrue(watchdog.shouldRestart(5100));
        watchdog.onStreamStart(5200);
        watchdog.onGyro(10500);
        assertFalse(watchdog.shouldRestart(10500));

        for (long now = 11000; now <= 16000; now += 500) {
            watchdog.onGyro(now);
            watchdog.onAccel(now);
            assertFalse(watchdog.shouldRestart(now));
        }
        watchdog.onAccel(21000);
        assertTrue(watchdog.shouldRestart(21000));
    }
}
