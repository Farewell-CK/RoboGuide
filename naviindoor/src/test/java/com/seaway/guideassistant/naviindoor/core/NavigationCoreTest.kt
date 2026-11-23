package com.seaway.guideassistant.naviindoor.core

import org.junit.Assert.*
import org.junit.Test

class NavigationCoreTest {
    private fun v(axis: Int = 0) = List(768) { if (it == axis) 1.0 else 0.0 }
    private fun point(i: Int, turn: Turn = Turn.straight, heading: Double = 0.0) = Waypoint("p" + i, i, v(), heading, turn)
    private fun path(points: List<Waypoint>, from: String = "a", to: String = "b") = IndoorPath(from + to, from + to, from, to, points)
    @Test fun validatesFeatureDimensionsAndValues() {
        assertTrue(usableEmbedding(v())); assertFalse(usableEmbedding(List(767) { 1.0 }))
        assertFalse(usableEmbedding(List(768) { 0.0 })); assertFalse(usableEmbedding(v().toMutableList().apply { this[0] = Double.NaN }))
    }
    @Test fun cosineIsNormalized() { assertEquals(1.0, cosine(v(), v().map { it * 3 }), 1e-9); assertEquals(0.0, cosine(v(), v(1)), 1e-9) }
    @Test fun badFeaturesCannotMatch() { assertTrue(cosine(emptyList(), v()).isNaN()) }
    @Test fun headingCrossesNorthByShortArc() { assertEquals(2.0, relativeHeading(359.0, 1.0), 0.0); assertEquals(-2.0, relativeHeading(1.0, 359.0), 0.0) }
    @Test fun positiveHeadingIsRight() { assertEquals(90.0, relativeHeading(0.0, 90.0), 0.0); assertEquals(-90.0, relativeHeading(0.0, 270.0), 0.0) }
    @Test fun departureRequiresFiveDegrees() { assertTrue(departureAligned(356.0, 1.0)); assertFalse(departureAligned(355.0, 1.0)); assertFalse(departureAligned(null, 1.0)) }
    @Test fun leftTurnNeedsCorrectRotationAndEndHeading() {
        assertTrue(turnConfirmed(Turn.left, 0.0, 270.0, 280.0))
        assertFalse(turnConfirmed(Turn.left, 0.0, 270.0, 90.0)); assertFalse(turnConfirmed(Turn.left, 0.0, 270.0, 330.0))
    }
    @Test fun rightTurnAcrossNorthWorks() { assertTrue(turnConfirmed(Turn.right, 340.0, 70.0, 60.0)); assertFalse(turnConfirmed(Turn.right, 340.0, 70.0, null)) }
    @Test fun infersPersistentTurn() { assertEquals(Turn.left, inferTurns(listOf(point(0), point(1), point(2, heading = 270.0), point(3, heading = 270.0)))[2].turn) }
    @Test fun doesNotInferCompassSpike() { assertEquals(Turn.straight, inferTurns(listOf(point(0), point(1, heading = 90.0), point(2)))[1].turn) }
    @Test fun storedTurnWins() { assertEquals(Turn.left, inferTurns(listOf(point(0), point(1, Turn.left), point(2)))[1].turn) }
    @Test fun criticalThresholdIncludesPreTurnAndEndpoint() {
        val points = listOf(point(0), point(1), point(2, Turn.right), point(3))
        assertEquals(.7, waypointThreshold(points, 0), 0.0)
        for (i in 1..3) assertEquals(.8, waypointThreshold(points, i), 0.0)
    }
    @Test fun windowStopsBeforePreTurn() { val p = (0..6).map { point(it, if (it == 4) Turn.left else Turn.straight) }; assertEquals(2, forwardLimit(p, 1)); assertEquals(0, forwardLimit(p, 0)); assertEquals(3, forwardLimit(p, 3)) }
    @Test fun anchorRequiresTwoFreshObservations() {
        val t = WaypointTracker()
        assertEquals(MatchReason.confirming, t.observe(0, mapOf(0 to .9), .7, 1000).reason)
        assertEquals(MatchReason.repeatedFrame, t.observe(0, mapOf(0 to .9), .7, 1400).reason)
        assertTrue(t.observe(0, mapOf(0 to .9), .7, 1800).accepted)
    }
    @Test fun lowFrameBreaksCriticalConfirmation() {
        val t = WaypointTracker(); t.observe(0, mapOf(0 to .9), .8, 1000)
        t.observe(0, mapOf(0 to .7), .8, 1800)
        assertFalse(t.observe(0, mapOf(0 to .9), .8, 2600).accepted)
    }
    @Test fun ordinaryPointsShareRecentMovement() {
        val t = WaypointTracker()
        for (i in 1..4) assertTrue(t.observe(i, mapOf(i to .75), .7, i * 1000L, movementCount = 1).accepted)
        assertEquals(MatchReason.waitingMovement, t.observe(5, mapOf(5 to .9), .7, 5000, movementCount = 1).reason)
    }
    @Test fun stationaryHallwayCannotConsumeWholeRoute() {
        val t = WaypointTracker()
        assertEquals(MatchReason.waitingMovement, t.observe(1, mapOf(1 to .99, 2 to .99), .7, 1000, 2).reason)
    }
    @Test fun noCompetingReferenceMarginForSimilarHallway() {
        val t = WaypointTracker()
        val d = t.observe(1, mapOf(0 to .907, 1 to .79997, 2 to .874718, 3 to .85225), .7, 1000, 3, 1)
        assertTrue(d.accepted); assertEquals(2, d.candidate)
    }
    @Test fun movementLeaseExpiresWithoutRefresh() {
        val t = WaypointTracker(); assertTrue(t.observe(1, mapOf(1 to .9), .7, 1000, movementCount = 1).accepted)
        assertEquals(MatchReason.waitingMovement, t.observe(2, mapOf(2 to .9), .7, 22_000, movementCount = 1).reason)
    }
    @Test fun newMovementRenewsLease() {
        val t = WaypointTracker(); t.observe(1, mapOf(1 to .9), .7, 1000, movementCount = 1)
        assertTrue(t.observe(2, mapOf(2 to .9), .7, 22_000, movementCount = 2).accepted)
    }
    @Test fun turnInPlaceDoesNotRequireAnExtraStep() {
        val t = WaypointTracker(); assertFalse(t.observe(2, mapOf(2 to .9), .8, 1000, turnInPlace = true).accepted)
        assertTrue(t.observe(2, mapOf(2 to .9), .8, 1800, turnInPlace = true).accepted)
        assertTrue(t.observe(3, mapOf(3 to .9), .7, 2600).accepted)
    }
    @Test fun invalidAndOldFramesNeverAdvance() {
        val t = WaypointTracker()
        assertEquals(MatchReason.invalid, t.observe(0, mapOf(0 to Double.NaN), .7, 1000).reason)
        assertEquals(MatchReason.repeatedFrame, t.observe(0, mapOf(0 to .9), .7, 900).reason)
    }
    @Test fun acceptedReferenceCannotBeAcceptedAgain() {
        val t = WaypointTracker(); t.observe(0, mapOf(0 to .9), .7, 1000); t.observe(0, mapOf(0 to .9), .7, 1800)
        assertEquals(MatchReason.repeatedFrame, t.observe(0, mapOf(0 to .9), .7, 2600).reason)
    }
    @Test fun rankDistinctLocationsInsteadOfRepeatedViews() {
        val refs = listOf(LocationReference("1", "a", "A", v()), LocationReference("2", "a", "A", v()), LocationReference("3", "b", "B", v(1)))
        assertEquals(listOf("a", "b"), rankLocations(v(), refs).map { it.reference.nodeId })
    }
    @Test fun navigationRequiresOrientationBeforeMatching() {
        val session = NavigationSession(path(listOf(point(0), point(1))), "电梯")
        assertEquals(NavigationPhase.orienting, session.observe(v(), 1000, 1, 0.0).phase)
        assertEquals(NavigationPhase.navigating, session.orient(0.0).phase)
    }
    @Test fun endpointRequiresFreshVisualConfirmationAndSignalsArrival() {
        val s = NavigationSession(path(listOf(point(0), point(1))), "电梯口"); s.orient(0.0)
        s.observe(v(), 1000, 0, 0.0); s.observe(v(), 1800, 0, 0.0)
        s.confirmPosition()
        assertEquals(NavigationPhase.navigating, s.observe(v(), 2600, 0, 0.0).phase)
        val result = s.observe(v(), 3400, 0, 0.0)
        assertEquals(NavigationPhase.arrived, result.phase); assertTrue(result.text.contains("已到达电梯口"))
    }
    @Test fun manualPositionCannotBypassBadImage() {
        val s = NavigationSession(path(listOf(point(0), point(1))), "电梯口"); s.orient(0.0); s.confirmPosition()
        assertEquals(MatchReason.belowThreshold, s.observe(v(1), 1000, 0, 0.0).decision?.reason)
    }
    @Test fun stoppedNavigationDoesNotAdvance() {
        val s = NavigationSession(path(listOf(point(0), point(1))), "电梯"); s.stop()
        assertEquals(NavigationPhase.stopped, s.observe(v(), 1000, 5, 0.0).phase); assertEquals(0, s.target)
    }
    @Test fun graphSupportsMultipleDirectedSegments() {
        val route = planRoute(listOf(path(listOf(point(0), point(1)), "a", "b"), path(listOf(point(0), point(1)), "b", "c")), "a", "c")!!
        assertEquals("c", route.endId); route.validate(); assertEquals(4, route.waypoints.size)
    }
    @Test fun graphDoesNotInventReverseTravel() { assertNull(planRoute(listOf(path(listOf(point(0), point(1)))), "b", "a")) }
    @Test fun rejectsGapsInRecordedRoute() { assertThrows(IllegalArgumentException::class.java) { path(listOf(point(0), point(2))).validate() } }
    @Test fun responseFromOldPageIsRejected() { val gate = SessionGate(); val old = gate.generation; gate.invalidate(); assertFalse(gate.accepts(old)); assertTrue(gate.accepts(gate.generation)) }
}
