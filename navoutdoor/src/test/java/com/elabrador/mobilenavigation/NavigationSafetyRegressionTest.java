package com.elabrador.mobilenavigation;

import org.junit.Test;
import java.util.*;
import static org.junit.Assert.*;

public class NavigationSafetyRegressionTest {
    @Test public void prefixRejectsDiagonalCornerAndHiddenSegmentObstacle(){
        int[][] map=new int[10][10];map[1][2]=100;
        assertTrue(LocalPlanner.safePrefix(Arrays.asList(new float[]{2,2}),map,1,0,0,1,1).isEmpty());
        map[1][2]=0;map[4][4]=100;
        assertEquals(1,LocalPlanner.safePrefix(Arrays.asList(new float[]{2,2},new float[]{7,7}),map,1,0,0,1,1).size());
    }
    @Test public void cyanCannotEraseHardObstacle(){
        int[][] map=new int[10][10];map[3][3]=100;
        assertEquals(100,LocalPlanner.visualize(map,Arrays.asList(new float[]{3,3}),1,0,0)[3][3]);
    }
    @Test public void distantObstacleTruncatesAndNewNearObstacleInvalidatesHeldPrefix(){
        int[][] map=new int[80][80];for(int[] r:map)Arrays.fill(r,20);
        LocalPlanner p=new LocalPlanner();p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1);
        map[65][40]=100;
        LocalPlanner.PathResult held=p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1);
        assertTrue(held.success);assertTrue(held.worldPath.get(held.worldPath.size()-1)[1]<5.1f);
        Arrays.fill(map[44],100);
        LocalPlanner.PathResult blocked=p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1);
        assertFalse(blocked.success);assertTrue(blocked.worldPath.isEmpty());
    }
    @Test public void randomNewMapsAlwaysRevalidateEntireOutput(){
        Random random=new Random(834);LocalPlanner planner=new LocalPlanner();
        for(int frame=0;frame<40;frame++){
            int[][] map=new int[25][25];for(int[] row:map)Arrays.fill(row,20);
            for(int i=0;i<35;i++)map[random.nextInt(25)][random.nextInt(25)]=100;
            LocalPlanner.PathResult p=planner.plan(map,1,-12,-12,0,0,frame%3-1,1);
            assertEquals(p.worldPath.size(),LocalPlanner.safePrefix(p.worldPath,
                    LocalPlannerGrid.preprocess(map),1,-12,-12,0,0).size());
        }
    }
    private FrameEvidence evidence(long millis){return new FrameEvidence(millis/1000.,1,millis,millis);}
    @Test public void redrawCannotCountAsNewMapOrExtendObservationAge(){
        ObservationRefreshTracker t=new ObservationRefreshTracker();FrameEvidence f=evidence(1000);
        assertTrue(t.observe(f,1100));assertFalse(t.observe(f,1200));
        assertFalse(t.observe(evidence(900),1300));assertFalse(t.observe(f,2600));
        assertTrue(t.observe(evidence(2700),2800));assertEquals(1700,t.intervalMillis);
    }
    @Test public void adjacentDepthFrameAndUnknownClockAreRejected(){
        assertTrue(FramePairTiming.valid(true,.05));assertTrue(FramePairTiming.valid(true,20));
        assertFalse(FramePairTiming.valid(true,33.3));assertFalse(FramePairTiming.valid(false,.05));
        assertFalse(FramePairTiming.valid(true,Double.NaN));
    }
    @Test public void polylineIsCompleteAndAlreadyGcjCoordinatesAreNotConvertedAgain(){
        List<AmapRouteClient.GeoPoint> p=AmapRouteClient.parsePolyline("116.403672,39.910634;116.404,39.911;116.405,39.912");
        assertEquals(3,p.size());assertEquals(116.403672,p.get(0).longitude,1e-9);
        assertEquals(39.910634,p.get(0).latitude,1e-9);
    }
    @Test public void malformedRouteMustNotBecomeStraightShortcut(){
        for(String s:Arrays.asList("","116,39;bad;116.1,39.1","NaN,39;116,39","200,39;116,39")){
            try{AmapRouteClient.parsePolyline(s);fail(s);}catch(IllegalArgumentException expected){}
        }
    }
    @Test public void stopImmediateAndRecoveryNeedsOneAndHalfSecondsOfFreshFrames(){
        GuidanceStabilizer g=new GuidanceStabilizer();
        assertEquals("直走",g.updateValidated("直走",true,1000,evidence(1000)));
        assertEquals("停止",g.updateValidated("停止",true,1001,evidence(1001)));
        assertEquals("停止",g.updateValidated("左转",true,1100,evidence(1100)));
        assertEquals("停止",g.updateValidated("左转",true,1500,evidence(1100)));
        assertEquals("停止",g.updateValidated("左转",true,1501,evidence(1501)));
        assertEquals("停止",g.updateValidated("左转",true,2599,evidence(2599)));
        assertEquals("左转",g.updateValidated("左转",true,2600,evidence(2600)));
    }
    @Test public void ordinaryTurnCannotPreemptButStaleEvidenceStopsImmediately(){
        GuidanceStabilizer g=new GuidanceStabilizer();
        assertEquals("左转",g.updateValidated("左转",true,1000,evidence(1000)));
        assertEquals("左转",g.updateValidated("右转",true,1100,evidence(1100)));
        assertEquals("左转",g.updateValidated("右转",true,1400,evidence(1400)));
        assertEquals("停止",g.updateValidated("右转",true,3000,evidence(1400)));
    }
    @Test public void weightedHeuristicMatchesDijkstraCost(){
        // Compare base A* to independent Dijkstra on random eight-connected grids.
        Random rng=new Random(84);
        for(int trial=0;trial<20;trial++){
            int n=12;int[][] map=new int[n][n];Env env=new Env();
            for(int r=0;r<n;r++)for(int c=0;c<n;c++)map[r][c]=rng.nextInt(6)==0?100:20+rng.nextInt(20);
            map[0][0]=map[n-1][n-1]=20;env.obsMapSet(n,n,map);
            List<int[]> path=new AStar(new int[]{0,0},new int[]{n-1,n-1},map,env.obstacles,AStar.Heuristic.EUCLIDEAN,3).searching();
            double[] d=new double[n*n];Arrays.fill(d,Double.POSITIVE_INFINITY);d[0]=0;
            PriorityQueue<double[]> q=new PriorityQueue<>(Comparator.comparingDouble(a->a[1]));q.add(new double[]{0,0});
            while(!q.isEmpty()){
                double[] a=q.poll();int k=(int)a[0],r=k/n,c=k%n;if(a[1]!=d[k])continue;
                for(int dr=-1;dr<=1;dr++)for(int dc=-1;dc<=1;dc++){
                    int nr=r+dr,nc=c+dc;if((dr==0&&dc==0)||nr<0||nc<0||nr>=n||nc>=n||map[nr][nc]==100)continue;
                    if(dr!=0&&dc!=0&&(map[nr][c]==100||map[r][nc]==100))continue;
                    double cost=3*Math.hypot(dr,dc)+map[nr][nc]+.01*Math.abs(nr-nc)/Math.sqrt(2);
                    if(d[k]+cost<d[nr*n+nc]){d[nr*n+nc]=d[k]+cost;q.add(new double[]{nr*n+nc,d[nr*n+nc]});}
                }
            }
            if(!Double.isFinite(d[n*n-1]))assertTrue(path.isEmpty());
            else{
                assertFalse(path.isEmpty());double total=0;
                for(int i=path.size()-2;i>=0;i--){int[] a=path.get(i+1),b=path.get(i);
                    total+=3*Math.hypot(a[0]-b[0],a[1]-b[1])+map[b[0]][b[1]]+.01*Math.abs(b[0]-b[1])/Math.sqrt(2);}
                assertEquals(d[n*n-1],total,1e-6);
            }
        }
    }
}
