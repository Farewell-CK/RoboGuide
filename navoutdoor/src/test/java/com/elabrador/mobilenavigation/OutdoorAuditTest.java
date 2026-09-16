package com.elabrador.mobilenavigation;

import java.util.Arrays;
import java.util.Collections;
import org.junit.Test;
import static org.junit.Assert.*;

public class OutdoorAuditTest {
    static final double M=180/Math.PI/6371000;
    private DynamicHeadingCalibrator fit(int samples,int mode){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<samples;i++){
            double north=i*2, east=0;
            if(mode==1)east=(i%2==0?8:-8); // contradictory GPS scatter
            if(mode==2)north*=0.25; // visually correct-looking but wrong displacement scale
            if(mode==3)north=0; // GPS stuck while VINS moves
            if(mode==4){north=0;east=i*2;} // east movement maps to VINS +Y
            c.update(1+north*M,10+east*M/Math.cos(Math.toRadians(1)),3,0,i*2,true);
        }
        return c;
    }
    private VinsMono.Pose pose(double time,double yaw){
        double half=Math.toRadians(yaw)/2;
        return new VinsMono.Pose(new double[]{0,0,0,0,0,Math.sin(half),Math.cos(half),0,0,0,1,time},
                new double[]{1,0,0,0,1,0,0,0,1},new double[]{0,0,0});
    }
    @Test public void cardinalDirectionsAndCameraRotationKeepLeftRightSign(){
        DynamicHeadingCalibrator c=fit(19,0);assertTrue(c.isReady());
        assertEquals(0,c.relativeTargetDegrees(0,pose(1,0)),.01);
        assertEquals(90,c.relativeTargetDegrees(90,pose(1,0)),.01);
        assertEquals(-90,c.relativeTargetDegrees(270,pose(1,0)),.01);
        assertEquals(90,c.relativeTargetDegrees(0,pose(1,90)),.01);
        c=fit(19,4);assertTrue(c.isReady());
        assertEquals(0,c.relativeTargetDegrees(90,pose(1,0)),.01);
        assertEquals(-90,c.relativeTargetDegrees(0,pose(1,0)),.01);
    }
    @Test public void calibrationDoesNotAcceptScatterScaleErrorOrFrozenGps(){
        for(int mode=1;mode<=3;mode++)assertFalse("mode "+mode,fit(30,mode).isReady());
    }
    @Test public void firstGoodFitAtSeventeenSamplesCompletesWithoutExtraConfirmation(){
        assertFalse(fit(16,0).isReady()); assertTrue(fit(17,0).isReady());
        assertEquals(17,fit(25,0).sampleCount());
    }
    @Test public void badAccuracyCannotCalibrate(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<30;i++)c.update(1+i*2*M,10,-1,0,i*2,true);
        assertFalse(c.isReady());
    }
    @Test public void poseHistoryRejectsOutageButInterpolatesNormalFrames(){
        VinsPoseHistory h=new VinsPoseHistory();h.add(pose(1,0));h.add(pose(1.1,10));
        assertNotNull(h.at(1.05));h.add(pose(3,20));
        assertNull(h.at(2));assertNull(h.atOrNearest(3.3,.1));
    }
    @Test public void oldObservationCannotBeRenewedByRepeatedCueOrReprojection(){
        FrameEvidence f=new FrameEvidence(10,1,10000,1000);
        GuidanceStabilizer g=new GuidanceStabilizer();
        assertEquals("直走",g.updateWithEvidence("直走",true,1000,f));
        assertEquals("直走",g.updateWithEvidence("直走",true,2499,f));
        assertEquals("停止",g.updateWithEvidence("直走",true,2500,f));
        assertEquals("停止",g.updateWithEvidence("",true,2501,f));
        assertEquals("",g.updateWithEvidence("直走",false,3000,f));
    }
    @Test public void captureLatencyCountsAndInvalidClockFailsClosed(){
        FrameEvidence delayed=new FrameEvidence(10,1,11000,5000);
        assertEquals(1000,delayed.ageMillis(5000));assertFalse(delayed.fresh(5500));
        assertFalse(new FrameEvidence(Double.NaN,1,11000,5000).fresh(5000));
        assertFalse(new FrameEvidence(20,1,11000,5000).fresh(5000));
    }
    @Test public void gpsRejectsJumpDuplicateBadAccuracyAndOldFixButAllowsStationaryUpdates(){
        OutdoorFixGate g=new OutdoorFixGate();
        assertTrue(g.accept(1,10,3,1_000_000_000L,1_000_000_000L));
        assertFalse(g.accept(1+100*M,10,3,2_000_000_000L,2_000_000_000L));
        assertTrue(g.accept(1,10,3,2_000_000_000L,2_000_000_000L));
        assertFalse(g.accept(1,10,3,2_000_000_000L,2_000_000_000L));
        assertFalse(g.accept(1,10,30,3_000_000_000L,3_000_000_000L));
        assertFalse(g.accept(1,10,3,3_000_000_000L,9_000_000_000L));
    }
    private RouteFollower loop(){
        java.util.List<AmapRouteClient.GeoPoint> p=Arrays.asList(
            point(0,0),point(100,0),point(100,5),point(0,5),point(0,30));
        RouteFollower f=new RouteFollower();
        f.setRoute(new AmapRouteClient.RouteResult("终点",0,0,230,200,"北行",0,Collections.emptyList(),p));
        return f;
    }
    private AmapRouteClient.GeoPoint point(double n,double e){return new AmapRouteClient.GeoPoint(n*M,e*M,"");}
    @Test public void adjacentReturnLegCannotStealRouteProgress(){
        RouteFollower f=loop();
        assertEquals(0,f.update(0,0,3,Float.NaN,1_000_000_000L).targetBearingDegrees,1);
        RouteFollower.Guidance next=f.update(2*M,4*M,5,Float.NaN,2_000_000_000L);
        assertNotNull(next);assertEquals(0,next.targetBearingDegrees,1);assertTrue(next.remainingMeters>200);
    }
    @Test public void repeatedSameGpsCannotRatchetForwardOrDeclareArrival(){
        RouteFollower f=loop();f.update(0,0,3,Float.NaN,1_000_000_000L);
        RouteFollower.Guidance g=f.update(10*M,0,3,Float.NaN,2_000_000_000L);
        for(int i=0;i<100;i++)assertSame(g,f.update(90*M,0,3,Float.NaN,2_000_000_000L));
        assertFalse(g.arrived);assertNull(f.update(0,0,3,Float.NaN,1_500_000_000L));
    }
    @Test public void rightGeographicTargetProducesRightPlannerCue(){
        DynamicHeadingCalibrator c=fit(19,0);
        float relative=c.relativeTargetDegrees(90,pose(1,0));
        int[][] grid=new int[80][80];for(int[] row:grid)Arrays.fill(row,20);
        LocalPlanner.PathResult result=new LocalPlanner().plan(grid,.2f,-7.9f,-7.9f,
                0,0,(float)Math.sin(Math.toRadians(relative)),(float)Math.cos(Math.toRadians(relative)));
        assertTrue(result.success);assertFalse(result.blocked);
        assertEquals("右转",NavigationCue.fromPlan(result.planned,result.success,result.blocked,result.steeringDegrees));
    }
    @Test public void initialFixNearReturnLegStillStartsOnFirstRouteLeg(){
        RouteFollower f=loop();
        RouteFollower.Guidance g=f.update(2*M,4*M,5,Float.NaN,1_000_000_000L);
        assertNotNull(g);assertEquals(0,g.targetBearingDegrees,1);
    }
    @Test public void alignedModeAcceptsCoarsePhoneLocationsAndAdvancesRoute(){
        RouteFollower f=loop();
        RouteFollower.Guidance first=f.update(0,0,33,Float.NaN,1_000_000_000L);
        RouteFollower.Guidance next=f.update(6*M,0,40.2f,Float.NaN,3_000_000_000L);
        assertNotNull(first);assertNotNull(next);
        assertTrue(next.remainingMeters<first.remainingMeters);
        assertEquals(0,next.targetBearingDegrees,1);
        assertFalse(next.arrived);
    }
    @Test public void alignedModeRejectsInvalidCoordinatesWithoutConsumingFix(){
        RouteFollower f=loop();
        assertNull(f.update(Double.NaN,0,33,Float.NaN,1_000_000_000L));
        assertNull(f.update(91,0,33,Float.NaN,1_000_000_000L));
        assertNull(f.update(0,181,33,Float.NaN,1_000_000_000L));
        assertNotNull(f.update(0,0,33,Float.NaN,1_000_000_000L));
    }
    @Test public void alignedModeArrivalDoesNotRequireEightMeterAccuracy(){
        RouteFollower f=new RouteFollower();
        f.setRoute(new AmapRouteClient.RouteResult("终点",0,0,20,20,"北行",0,
                Collections.emptyList(),Arrays.asList(point(0,0),point(20,0))));
        assertFalse(f.update(0,0,33,Float.NaN,1_000_000_000L).arrived);
        assertTrue(f.update(15*M,0,33,Float.NaN,6_000_000_000L).arrived);
    }
}
