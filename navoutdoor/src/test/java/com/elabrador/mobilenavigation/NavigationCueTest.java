package com.elabrador.mobilenavigation;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class NavigationCueTest {
    @Test public void coversEverySpokenDirection() {
        assertEquals("", NavigationCue.fromPlan(false, false, false, Float.NaN));
        assertEquals("停止", NavigationCue.fromPlan(true, false, false, 0f));
        assertEquals("停止", NavigationCue.fromPlan(true, true, true, 0f));
        assertEquals("直走", NavigationCue.fromPlan(true, true, false, 10f));
        assertEquals("左转", NavigationCue.fromPlan(true, true, false, 45f));
        assertEquals("右转", NavigationCue.fromPlan(true, true, false, -45f));
        assertEquals("向后转", NavigationCue.fromPlan(true, true, false, 135f));
        assertEquals("向后转", NavigationCue.fromPlan(true, true, false, -179f));
    }
}
