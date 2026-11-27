package com.seaway.guideassistant.ble.cane

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import kotlin.math.PI

class CaneAttitudeTest {

    @Test
    fun peakLevelBoundaries() {
        assertEquals(0, CaneAttitude.peakLevel(0))
        assertEquals(1, CaneAttitude.peakLevel(1))
        assertEquals(1, CaneAttitude.peakLevel(25))
        assertEquals(2, CaneAttitude.peakLevel(26))
        assertEquals(3, CaneAttitude.peakLevel(75))
        assertEquals(4, CaneAttitude.peakLevel(76))
        assertEquals(4, CaneAttitude.peakLevel(100))
    }

    @Test
    fun sectorMapsLeftFrontRight() {
        assertEquals(PI, CaneAttitude.sectorToRadians(0)!!, 1e-9)
        assertEquals(PI / 2, CaneAttitude.sectorToRadians(900)!!, 1e-9)
        assertEquals(0.0, CaneAttitude.sectorToRadians(1800)!!, 1e-9)
        assertNull(CaneAttitude.sectorToRadians(CaneProtocol.SECTOR_INVALID))
    }

    @Test
    fun invalidValues() {
        assertNull(CaneAttitude.reachOrNull(255))
        assertEquals(80, CaneAttitude.reachOrNull(80))
        assertEquals(90f, CaneAttitude.tiltDegrees(1200), 0f)
    }

    @Test
    fun rippleGrowsWithLevel() {
        assert(CaneAttitude.rippleDurationMs(4) > CaneAttitude.rippleDurationMs(1))
        assert(CaneAttitude.rippleRadiusFraction(4) > CaneAttitude.rippleRadiusFraction(1))
    }

    @Test
    fun stateNames() {
        assertEquals("碰撞中", CaneAttitude.stateName(3))
        assertEquals("坑/洼地", CaneAttitude.stateName(5))
        assertEquals("未知", CaneAttitude.stateName(9))
    }
}
