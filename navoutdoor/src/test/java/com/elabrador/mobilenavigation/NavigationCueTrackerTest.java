package com.elabrador.mobilenavigation;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class NavigationCueTrackerTest {
    @Test public void reportsStraightBelowThirtyDegrees() {
        NavigationCue.Tracker tracker = new NavigationCue.Tracker();
        assertEquals("直走", tracker.update(true, true, false, 8f));
        assertEquals("直走", tracker.update(true, true, false, -8f));
    }

    @Test public void quantizesTurnMagnitudeToTenDegreeSteps() {
        NavigationCue.Tracker tracker = new NavigationCue.Tracker();
        assertEquals("左转 30°", tracker.update(true, true, false, 34f));
        assertEquals("直走", tracker.update(true, true, false, -27f));
    }

    @Test public void holdsAdjacentStepsWithinSameDirection() {
        NavigationCue.Tracker tracker = new NavigationCue.Tracker();
        assertEquals("左转 30°", tracker.update(true, true, false, 34f));
        // Quantized 40 differs by 10 < 20 hold: keep the shown step.
        assertEquals("左转 30°", tracker.update(true, true, false, 44f));
        // Quantized 60 differs by 30: update.
        assertEquals("左转 60°", tracker.update(true, true, false, 56f));
    }

    @Test public void categoryChangesApplyImmediately() {
        NavigationCue.Tracker tracker = new NavigationCue.Tracker();
        assertEquals("直走", tracker.update(true, true, false, 5f));
        assertEquals("直走", tracker.update(true, true, false, 25f));
        assertEquals("直走", tracker.update(true, true, false, 4f));
        assertEquals("向后转", tracker.update(true, true, false, 140f));
    }

    @Test public void stopResetsHeldState() {
        NavigationCue.Tracker tracker = new NavigationCue.Tracker();
        assertEquals("左转 30°", tracker.update(true, true, false, 34f));
        assertEquals("停止", tracker.update(true, false, false, 0f));
        // Fresh state after stop: no hold against the pre-stop step.
        assertEquals("左转 50°", tracker.update(true, true, false, 45f));
    }

    @Test public void unplannedAndUnfiniteStayEmpty() {
        NavigationCue.Tracker tracker = new NavigationCue.Tracker();
        assertEquals("", tracker.update(false, true, false, 30f));
        assertEquals("", tracker.update(true, true, false, Float.NaN));
    }
}
