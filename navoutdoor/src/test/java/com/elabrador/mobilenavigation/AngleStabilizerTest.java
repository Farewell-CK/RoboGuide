package com.elabrador.mobilenavigation;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public class AngleStabilizerTest {
    @Test public void ignoresSingleOutlierInsideWindow() {
        AngleStabilizer stabilizer = new AngleStabilizer(3, 15f);
        assertEquals(0f, stabilizer.update(0f), 1e-6f);
        assertEquals(0f, stabilizer.update(2f), 1e-6f);
        // Medoid of {0, 2, 30} is 2; 2° away from stable 0° -> hold.
        assertEquals(0f, stabilizer.update(30f), 1e-6f);
        assertEquals(0f, stabilizer.update(3f), 1e-6f);
    }

    @Test public void shiftsOnlyBeyondThreshold() {
        AngleStabilizer stabilizer = new AngleStabilizer(3, 15f);
        assertEquals(0f, stabilizer.update(0f), 1e-6f);
        // Mixed window keeps the medoid on the old value; within threshold, hold at 0.
        assertEquals(0f, stabilizer.update(16f), 1e-6f);
        assertEquals(0f, stabilizer.update(16f), 1e-6f);
        // Fully-replaced window moved 16° away: shift.
        assertEquals(16f, stabilizer.update(16f), 1e-6f);
    }

    @Test public void wrapsAroundPlusMinus180() {
        AngleStabilizer stabilizer = new AngleStabilizer(2, 15f);
        stabilizer.update(175f);
        // -175 is only 10° away across the wrap; must not shift.
        assertEquals(175f, stabilizer.update(-175f), 1e-6f);
    }

    @Test public void resetClearsHistory() {
        AngleStabilizer stabilizer = new AngleStabilizer(3, 15f);
        stabilizer.update(40f);
        stabilizer.reset();
        assertEquals(90f, stabilizer.update(90f), 1e-6f);
    }

    @Test public void nonFiniteKeepsLastStableValue() {
        AngleStabilizer stabilizer = new AngleStabilizer(3, 15f);
        stabilizer.update(25f);
        assertEquals(25f, stabilizer.update(Float.NaN), 1e-6f);
    }
}
