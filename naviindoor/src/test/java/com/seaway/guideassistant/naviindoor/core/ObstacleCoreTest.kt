package com.seaway.guideassistant.naviindoor.core

import org.junit.Assert.*
import org.junit.Test

class ObstacleCoreTest {
    private fun chair() = ObstacleDetection(19, "椅子", "前方", "caution", .9, .1, listOf(.3,.3,.7,.8))
    @Test fun ordinaryObjectNeedsTwoFrames() { val c=ObstacleConfirmation(); assertTrue(c.update(listOf(chair()),1000).isEmpty()); assertEquals(1,c.update(listOf(chair()),1800).size) }
    @Test fun oldFrameCannotConfirm() { val c=ObstacleConfirmation(); c.update(listOf(chair()),1000); assertTrue(c.update(listOf(chair()),3500).isEmpty()) }
    @Test fun repeatedFrameCannotConfirm() { val c=ObstacleConfirmation(); c.update(listOf(chair()),1000); assertTrue(c.update(listOf(chair()),1000).isEmpty()) }
    @Test fun onlyConfidentCentralPersonCanWarnImmediately() { assertEquals(1,ObstacleConfirmation().update(listOf(chair().copy(label="行人")),1000).size); assertTrue(ObstacleConfirmation().update(listOf(chair().copy(label="行人",confidence=.7)),1800).isEmpty()) }
    @Test fun differentPositionsDoNotConfirm() { val c=ObstacleConfirmation(); c.update(listOf(chair()),1000); assertTrue(c.update(listOf(chair().copy(box=listOf(.0,.3,.2,.8))),1800).isEmpty()) }
    @Test fun missingDetectionClearsTrack() { val c=ObstacleConfirmation(); c.update(listOf(chair()),1000); c.update(emptyList(),1800); assertTrue(c.update(listOf(chair()),2600).isEmpty()) }
    @Test fun invalidBoxesAreRejected() { assertThrows(IllegalArgumentException::class.java) { chair().copy(box=listOf(.8,.3,.2,.8)).validate() } }
    @Test fun guidanceNeverClaimsMeasuredDistanceOrSafePath() { assertFalse(chair().message.contains("米")); assertTrue(chair().copy(label="楼梯").message.contains("停下确认")); assertTrue(chair().copy(label="门").message.contains("开闭")) }
}
