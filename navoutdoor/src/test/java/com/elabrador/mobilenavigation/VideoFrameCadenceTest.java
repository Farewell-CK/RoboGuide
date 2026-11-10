package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class VideoFrameCadenceTest {
    @Test public void cameraJitterDoesNotLowerThirtyToThirteenFps(){
        VideoFrameCadence cadence=new VideoFrameCadence();int frames=0;
        for(int i=0;i<300;i++){
            long capture=1_000_000_000L+i*1_000_000_000L/30+(i%3-1)*800_000L;
            if(cadence.accept(capture))frames++;
        }
        assertEquals(150,frames);
    }
    @Test public void gapsDoNotCauseCatchupBurstsAndNewSessionResets(){
        VideoFrameCadence cadence=new VideoFrameCadence();
        assertTrue(cadence.accept(1_000_000_000L));
        assertTrue(cadence.accept(2_000_000_000L));
        assertFalse(cadence.accept(2_010_000_000L));
        assertFalse(cadence.accept(1_999_000_000L));
        assertTrue(cadence.accept(2_067_000_000L));
        cadence.reset();assertTrue(cadence.accept(1_000_000_000L));
    }
}
