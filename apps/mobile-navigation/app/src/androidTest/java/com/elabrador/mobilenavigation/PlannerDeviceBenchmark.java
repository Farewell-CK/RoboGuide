package com.elabrador.mobilenavigation;

import android.util.Log;
import android.os.SystemClock;
import java.util.Arrays;

/** Same deterministic maps on the same phone; not a physical navigation test. */
final class PlannerDeviceBenchmark {
    static void run(){
        for(String scene:new String[]{"straight","detour","blocked","changing"}){
            ReadyLocalPlanner ready=new ReadyLocalPlanner();
            BeforeLocalPlanner before=new BeforeLocalPlanner();
            LocalPlanner after=new LocalPlanner();
            long[][] times=new long[3][20];
            for(int i=0;i<24;i++){
                int[][] map=new int[80][80];for(int[] row:map)Arrays.fill(row,20);
                if(!scene.equals("straight"))
                    for(int r=47;r<65;r++)for(int c=37;c<48;c++)map[r][c]=100;
                if(scene.equals("blocked"))Arrays.fill(map[45],100);
                float x=scene.equals("changing")?(i%2==0?.5f:-.5f):0;
                // Rotate execution order to reduce warmup/thermal ordering bias.
                for(int j=0;j<3;j++){
                    int version=(i+j)%3;long start=SystemClock.elapsedRealtimeNanos();
                    if(version==0)ready.plan(map,.2f,-7.9f,-7.9f,0,0,x,1);
                    else if(version==1)before.plan(map,.2f,-7.9f,-7.9f,0,0,x,1);
                    else {
                        LocalPlanner.PathResult p=after.plan(map,.2f,-7.9f,-7.9f,0,0,x,1);
                        if(LocalPlanner.safePrefix(p.worldPath,LocalPlannerGrid.preprocess(map),.2f,-7.9f,-7.9f,0,0).size()!=p.worldPath.size())
                            throw new AssertionError("Unsafe output: "+scene);
                    }
                    if(i>=4)times[version][i-4]=SystemClock.elapsedRealtimeNanos()-start;
                }
            }
            for(int v=0;v<3;v++){
                Arrays.sort(times[v]);Log.i("PlannerBench","scene="+scene+" version="+new String[]{"ready074","before083","after084"}[v]
                        +" n=20 p50_ms="+times[v][10]/1e6+" p95_ms="+times[v][19]/1e6);
            }
        }
    }
}
