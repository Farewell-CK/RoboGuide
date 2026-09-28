package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class CueTimingTest {
    private FrameEvidence frame(long time) { return new FrameEvidence(time/1000.,1,time,time); }
    private String cue(GuidanceStabilizer g,String text,long time) {
        return g.updateValidated(text,true,time,frame(time));
    }
    @Test public void turnsWaitFiveSecondsAndOnlyLatestWins() {
        GuidanceStabilizer g=new GuidanceStabilizer();
        assertEquals("直走",cue(g,"直走",0));
        assertEquals("直走",cue(g,"右转",500));
        assertEquals("直走",cue(g,"左转",1000));
        assertEquals("直走",cue(g,"直走",2500));
        assertEquals("直走",cue(g,"向后转",4999));
        assertEquals("右转",cue(g,"右转",5000));
        assertEquals("右转",cue(g,"左转",9999));
        assertEquals("左转",cue(g,"左转",10000));
    }
    @Test public void stopIsImmediateStraightCanReplaceTurnButCannotSkipStopRecovery() {
        GuidanceStabilizer g=new GuidanceStabilizer();
        cue(g,"直走",0);
        assertEquals("停止",cue(g,"停止",1));
        assertEquals("停止",cue(g,"直走",100));
        assertEquals("停止",cue(g,"左转",1000));
        assertEquals("停止",cue(g,"右转",1599));
        assertEquals("右转",cue(g,"右转",1600));
        assertEquals("直走",cue(g,"直走",1601));
        assertEquals("直走",cue(g,"左转",6600));
        assertEquals("左转",cue(g,"左转",6601));
        assertEquals("停止",cue(g,"停止",6602));
    }
    @Test public void straightPreemptsEveryTurnAndStartsAnotherFiveSecondHold() {
        for(String turn:new String[]{"左转 30°","右转 60°","向后转"}){
            GuidanceStabilizer g=new GuidanceStabilizer();
            assertEquals(turn,cue(g,turn,0));
            assertEquals("直走",cue(g,"直走",100));
            assertEquals("直走",cue(g,turn,5099));
            assertEquals(turn,cue(g,turn,5100));
        }
    }
    @Test public void hazardAndWaitingRestartContinuousSafetyConfirmation() {
        for(String interruption:new String[]{"停止",""}) {
            GuidanceStabilizer g=new GuidanceStabilizer();
            cue(g,"停止",0);cue(g,"直走",100);cue(g,"直走",1000);
            assertEquals("停止",cue(g,interruption,1500));
            assertEquals("停止",cue(g,"右转",1600));
            assertEquals("停止",cue(g,"右转",2500));
            assertEquals("停止",cue(g,"右转",3099));
            assertEquals("右转",cue(g,"右转",3100));
        }
    }
    @Test public void redrawsCannotCompleteRecovery() {
        GuidanceStabilizer g=new GuidanceStabilizer();cue(g,"停止",0);
        FrameEvidence f=frame(100);
        for(long t:new long[]{100,500,1000,1599,1600})
            assertEquals("停止",g.updateValidated("直走",true,t,f));
        assertEquals("停止",cue(g,"直走",1700));
        assertEquals("停止",cue(g,"直走",2700));
        assertEquals("直走",cue(g,"直走",3200));
    }
    @Test public void gapOrCameraRestartCannotCarryRecoveryAcrossDiscontinuity() {
        GuidanceStabilizer gap=new GuidanceStabilizer();cue(gap,"停止",0);cue(gap,"直走",100);
        assertEquals("停止",cue(gap,"直走",1600));
        cue(gap,"直走",2500);
        assertEquals("直走",cue(gap,"直走",3100));
        GuidanceStabilizer restarted=new GuidanceStabilizer();cue(restarted,"停止",0);
        cue(restarted,"直走",100);cue(restarted,"直走",1000);
        for(long t:new long[]{1600,2500,3099})
            assertEquals("停止",restarted.updateValidated("直走",true,t,new FrameEvidence(t/1000.,2,t,t)));
        assertEquals("直走",restarted.updateValidated("直走",true,3100,new FrameEvidence(3.1,2,3100,3100)));
    }
}
