package com.elabrador.mobilenavigation;

import org.junit.Test;
import java.util.*;
import static org.junit.Assert.*;

/** Desktop JVM timing only. No phone or camera performance claims. */
public class PlannerComparisonTest {
    @Test public void compareSameInputsOnDesktopJvm(){
        for(String scene:new String[]{"straight","detour","blocked","changing"}){
            ReadyLocalPlanner ready=new ReadyLocalPlanner();BeforeLocalPlanner before=new BeforeLocalPlanner();
            LocalPlanner after=new LocalPlanner();long[][] times=new long[3][20];
            for(int i=0;i<24;i++){
                int[][] map=new int[80][80];for(int[] row:map)Arrays.fill(row,20);
                if(!scene.equals("straight"))for(int r=47;r<65;r++)for(int c=37;c<48;c++)map[r][c]=100;
                if(scene.equals("blocked"))Arrays.fill(map[45],100);
                float x=scene.equals("changing")?(i%2==0?.5f:-.5f):0;
                for(int j=0;j<3;j++){
                    int v=(i+j)%3;long start=System.nanoTime();
                    if(v==0)ready.plan(map,.2f,-7.9f,-7.9f,0,0,x,1);
                    else if(v==1)before.plan(map,.2f,-7.9f,-7.9f,0,0,x,1);
                    else{
                        LocalPlanner.PathResult p=after.plan(map,.2f,-7.9f,-7.9f,0,0,x,1);
                        assertEquals(p.worldPath.size(),LocalPlanner.safePrefix(p.worldPath,
                                LocalPlannerGrid.preprocess(map),.2f,-7.9f,-7.9f,0,0).size());
                    }
                    if(i>=4)times[v][i-4]=System.nanoTime()-start;
                }
            }
            for(int v=0;v<3;v++){
                Arrays.sort(times[v]);System.out.println("DESKTOP_ONLY scene="+scene+" version="
                        +new String[]{"ready074","before083","after084"}[v]+" n=20 p50_ms="+times[v][10]/1e6
                        +" p95_ms="+times[v][19]/1e6);
            }
        }
    }
}
