package com.elabrador.mobilenavigation;

import org.junit.Test;
import java.util.Arrays;
import static org.junit.Assert.*;

public class LocalMapStabilizerTest {
    private static VinsMono.Pose pose(double x, double yaw) {
        double h = Math.toRadians(yaw) / 2;
        return new VinsMono.Pose(new double[]{x,0,0,0,0,Math.sin(h),Math.cos(h),0,0,0,1,1},
                new double[]{1,0,0,0,1,0,0,0,1}, new double[]{0,0,0});
    }

    private static LocalMapStabilizer.Snapshot frame(LocalMapStabilizer filter, int cost, long ms) {
        return filter.update(new int[]{cost}, pose(0,0), ms * 1_000_000L, ms / 1000.0);
    }

    @Test public void alternatingSoftCostsAndObstacleLabelsDoNotBlink() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,20,0);
        for (int i=1;i<=20;i++) assertEquals(20,frame(filter,i%2==0?20:0,i*50).costs[0]);
        frame(filter,100,1100);
        for (int i=1;i<=20;i++) assertEquals(100,frame(filter,i%2==0?100:20,1100+i*50).costs[0]);
    }

    @Test public void lowerCostRequiresElapsedTimeAndNewObservations() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,100,0);
        assertEquals(100,frame(filter,20,50).costs[0]);
        assertEquals(100,frame(filter,20,449).costs[0]);
        assertEquals(20,frame(filter,20,450).costs[0]);
        LocalMapStabilizer slow = new LocalMapStabilizer();
        frame(slow,100,0);
        frame(slow,20,50);
        assertEquals(100,frame(slow,20,500).costs[0]); // Only two lower observations.
        assertEquals(20,frame(slow,20,550).costs[0]);
    }

    @Test public void increasesAreImmediateEvenDuringRelease() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,60,0);
        frame(filter,20,50);
        assertEquals(100,frame(filter,100,100).costs[0]);
        assertEquals(100,frame(filter,20,450).costs[0]);
    }

    @Test public void unknownNeverInheritsFreeSpace() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,20,0);
        assertEquals(-1,frame(filter,-1,50).costs[0]);
        assertEquals(-1,frame(filter,20,100).reproject(new int[]{-1},pose(0,0),110_000_000L)[0]);
    }

    @Test public void briefMissingObstacleIsHeldButCannotPersistForever() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,100,0);
        assertEquals(100,frame(filter,-1,50).costs[0]);
        assertEquals(100,frame(filter,-1,200).costs[0]);
        assertEquals(-1,frame(filter,-1,450).costs[0]);
    }

    @Test public void missingFramesDoNotCountAsFreeSpaceConfirmation() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,100,0);
        frame(filter,-1,50);
        frame(filter,-1,200);
        assertEquals(100,frame(filter,20,450).costs[0]);
        frame(filter,20,600);
        assertEquals(20,frame(filter,20,850).costs[0]);
    }

    @Test public void repeatedSameFrameAndReprojectionCannotClearObstacle() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,100,0);
        LocalMapStabilizer.Snapshot snapshot = frame(filter,20,50);
        for (int i=1;i<100;i++) {
            assertSame(snapshot,filter.update(new int[]{20},pose(0,0),500_000_000L,.05));
            assertEquals(100,snapshot.reproject(new int[]{20},pose(0,0),500_000_000L)[0]);
        }
        assertEquals(100,frame(filter,20,500).costs[0]);
        assertEquals(20,frame(filter,20,550).costs[0]);
    }

    @Test public void movementRotationStalenessAndResetDiscardHistory() {
        for (int mode=0;mode<4;mode++) {
            LocalMapStabilizer filter = new LocalMapStabilizer();
            frame(filter,100,0);
            if (mode==3) filter.clear();
            VinsMono.Pose current = pose(mode==0?.03:0,mode==1?1:0);
            long now = mode==2?800_000_000L:50_000_000L;
            assertEquals(20,filter.update(new int[]{20},current,now,1).costs[0]);
        }
    }

    @Test public void slowCumulativeMotionCannotKeepOldGridForever() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        frame(filter,100,0);
        assertEquals(100,filter.update(new int[]{20},pose(.01,0),50_000_000L,.05).costs[0]);
        assertEquals(20,filter.update(new int[]{20},pose(.025,0),100_000_000L,.1).costs[0]);
    }

    @Test public void snapshotDoesNotBypassNewObstaclesOrFollowCameraRotation() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        LocalMapStabilizer.Snapshot snapshot = frame(filter,20,0);
        assertEquals(100,snapshot.reproject(new int[]{100},pose(0,0),50_000_000L)[0]);
        snapshot = frame(filter,100,50);
        assertEquals(20,snapshot.reproject(new int[]{20},pose(0,90),100_000_000L)[0]);
        assertEquals(100,snapshot.reproject(new int[]{20},pose(0,0),900_000_000L)[0]);
    }

    @Test public void freshWallStillStopsPlannerInFirstFrame() {
        LocalMapStabilizer filter = new LocalMapStabilizer();
        int[] raw = new int[80*80];
        Arrays.fill(raw,20);
        filter.update(raw,pose(0,0),0,0);
        Arrays.fill(raw,42*80,43*80,100);
        int[] stable = filter.update(raw,pose(0,0),50_000_000L,.05).costs;
        int[][] grid = new int[80][80];
        for(int row=0;row<80;row++) System.arraycopy(stable,row*80,grid[row],0,80);
        LocalPlanner.PathResult result = new LocalPlanner().plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        assertFalse(result.success);
        assertEquals("停止",NavigationCue.fromPlan(result.planned,result.success,result.blocked,result.steeringDegrees));
    }
}
