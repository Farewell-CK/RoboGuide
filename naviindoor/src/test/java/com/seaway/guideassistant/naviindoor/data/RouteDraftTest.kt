package com.seaway.guideassistant.naviindoor.data

import com.seaway.guideassistant.naviindoor.core.Turn
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import java.io.File

class RouteDraftTest {
    @get:Rule val files=TemporaryFolder()
    @Test fun rawSamplesSurviveReloadWithoutNetwork() {
        val draft=RouteDraft.create(files.root,"办公室到电梯","a","b",Profile("u","e","admin",null),"http://example.invalid/")
        draft.append(byteArrayOf(1,2,3),350.0); draft.append(byteArrayOf(4,5),80.0)
        val restored=RouteDraft.load(draft.directory)
        assertEquals(2,restored.manifest.samples.size); assertEquals(Turn.right,restored.manifest.samples[1].turn)
        assertArrayEquals(byteArrayOf(1,2,3),File(draft.directory,restored.manifest.samples[0].filename).readBytes())
        assertEquals("http://example.invalid",restored.manifest.database)
    }
    @Test fun manualTurnLabelWinsWithoutChangingCapturedHeading() {
        val d=RouteDraft.create(files.root,"r","a","b",Profile("u","e","admin",null),"http://example.invalid")
        d.append(byteArrayOf(1),270.0,Turn.left)
        assertEquals(Turn.left,d.manifest.samples[0].turn); assertEquals(270.0,d.manifest.samples[0].heading,0.0)
    }
}
