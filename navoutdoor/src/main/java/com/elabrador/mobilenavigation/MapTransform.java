package com.elabrador.mobilenavigation;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/** Direct port of local_planning.map_transform.octree2localprmap_height_ego. */
final class MapTransform {
    static final int WIDTH = 80, HEIGHT = 80;
    static final float RESOLUTION = 0.2f;
    private static final int MAPILLARY_ROAD_RGB = 0x804080;
    private static final int MAPILLARY_SIDEWALK_RGB = 0xf423e8;
    private static final float HARD_SEMANTIC_DEGREE = 0.9f;
    static final class Grid {
        final int[] cost;
        int[] displayCost;
        final byte[] height;
        int known;
        final float groundHeight;
        final int groundClearedCells;
        final int groundSupportCells;
        final int groundPositiveCostCells;
        final int groundCandidateCells;
        Grid(int[] cost, byte[] height, int known, float groundHeight, int groundClearedCells,
             int groundSupportCells, int groundPositiveCostCells, int groundCandidateCells) {
            this.cost = cost;
            this.displayCost=cost;
            this.height = height;
            this.known = known;
            this.groundHeight = groundHeight;
            this.groundClearedCells = groundClearedCells;
            this.groundSupportCells = groundSupportCells;
            this.groundPositiveCostCells = groundPositiveCostCells;
            this.groundCandidateCells = groundCandidateCells;
        }
    }
    private static volatile Map<Integer, Float> degreeConfig;
    private MapTransform() {}

    /** Source ego projection, retaining the user's three edited semantic degree values. */
    static Grid octree2localprmapSource(Context context,float[] leaves,
                                       float x,float y,float z,float yaw) throws Exception {
        return sourcePlanningGrid(leaves,x,y,z,yaw,loadConfig(context));
    }
    static Grid octree2localprmapSource(Context context,float[] leaves,
                                       float captureX,float captureY,float captureZ,
                                       float projectionX,float projectionY,float projectionYaw) throws Exception {
        return sourcePlanningGrid(leaves,captureX,captureY,captureZ,
                projectionX,projectionY,projectionYaw,loadConfig(context));
    }

    static Grid sourcePlanningGrid(float[] leaves,float x,float y,float z,float yaw,
                                   Map<Integer,Float> config) {
        return sourcePlanningGrid(leaves,x,y,z,x,y,yaw,config);
    }
    static Grid sourcePlanningGrid(float[] leaves,float x,float y,float z,
                                   float projectionX,float projectionY,float yaw,Map<Integer,Float> config) {
        // Upstream planning applies its filters in the world-aligned grid.
        // Rotate that filtered map into this Android planner's ego frame.
        Grid world=octree2localprmapSource(leaves,x,y,z,0,config);
        int[] filtered=sourceMedian(world.cost),edges=sourceHeightEdges(world.height);
        for(int i=0;i<filtered.length;i++)if(edges[i]>0)filtered[i]=100;
        int[][] worldRows=new int[HEIGHT][WIDTH];
        for(int r=0;r<HEIGHT;r++)System.arraycopy(filtered,r*WIDTH,worldRows[r],0,WIDTH);
        // Source dilation also precedes the Android coordinate adapter. Applying
        // its square kernel after rotation would change obstacle costs with yaw.
        int[][] planned=LocalPlannerGrid.preprocessSource(worldRows);
        int[] ego=new int[WIDTH*HEIGHT],display=new int[ego.length];byte[] heights=new byte[ego.length];
        java.util.Arrays.fill(ego,50);java.util.Arrays.fill(heights,(byte)-1);
        java.util.Arrays.fill(display,-1);
        double c=Math.cos(yaw),s=Math.sin(yaw);int known=0;
        for(int r=0;r<HEIGHT;r++)for(int q=0;q<WIDTH;q++) {
            double ex=-7.9+(q+.5)*.2,ey=-7.9+(r+.5)*.2;
            int col=(int)Math.floor((projectionX-x+c*ex-s*ey+7.9)/.2);
            int row=(int)Math.floor((projectionY-y+s*ex+c*ey+7.9)/.2);
            if(col<0||row<0||col>=WIDTH||row>=HEIGHT)continue;
            int i=r*WIDTH+q,j=row*WIDTH+col;ego[i]=planned[row][col];display[i]=filtered[j];
            heights[i]=world.height[j];if(filtered[j]>=0)known++;
        }
        Grid result=new Grid(ego,heights,known,Float.NaN,0,0,0,0);
        result.displayCost=display;
        return result;
    }

    static Grid octree2localprmapSource(float[] leaves,float locX,float locY,float locZ,float yaw,
                                       Map<Integer,Float> config) {
        int[] cost=new int[WIDTH*HEIGHT];byte[] height=new byte[cost.length];
        java.util.Arrays.fill(cost,-1);java.util.Arrays.fill(height,(byte)-1);
        double[] sums=new double[cost.length],maxHeight=new double[cost.length];
        int[] counts=new int[cost.length];java.util.Arrays.fill(maxHeight,-4.04);
        double c=Math.cos(yaw),s=Math.sin(yaw),origin=-7.9,resolution=.2;
        for(int n=0;n+7<leaves.length;n+=8) {
            double x=leaves[n],y=leaves[n+1],z=leaves[n+2];
            if(!Double.isFinite(x)||!Double.isFinite(y)||!Double.isFinite(z)||leaves[n+3]<.5f)continue;
            if(x<locX-7.5||x>locX+7.5||y<locY-7.5||y>locY+7.5||z<locZ-2.5||z>locZ)continue;
            Float degree=config.get(sourceLookupColor((int)leaves[n+4],(int)leaves[n+5],(int)leaves[n+6]));
            if(degree==null)continue;
            double dx=x-locX,dy=y-locY,dz=z-locZ;
            int col=(int)((c*dx+s*dy-origin)/resolution),row=(int)((-s*dx+c*dy-origin)/resolution);
            if(col<0||row<0||col>=WIDTH||row>=HEIGHT)continue;
            int cell=row*WIDTH+col;
            double metric=dz<=-.4&&dz> -2.5?1:dz<=-.2&&dz>-.4?1-Math.pow((dz+.4)/.2,2):0;
            sums[cell]+=degree*metric;counts[cell]++;maxHeight[cell]=Math.max(maxHeight[cell],dz);
        }
        int known=0;
        for(int i=0;i<cost.length;i++) {
            if(counts[i]>0) {cost[i]=(int)(sums[i]/counts[i]*100);known++;}
            height[i]=(byte)((Math.min(0,maxHeight[i])+4.04)*101/4.04-1);
        }
        return new Grid(cost,height,known,Float.NaN,0,0,0,0);
    }

    static int[] sourceMedian(int[] input) {
        int[] output=new int[input.length],window=new int[9];
        for(int r=0;r<HEIGHT;r++)for(int c=0;c<WIDTH;c++) {
            int n=0;
            for(int dr=-1;dr<=1;dr++)for(int dc=-1;dc<=1;dc++) {
                int rr=Math.max(0,Math.min(HEIGHT-1,r+dr)),cc=Math.max(0,Math.min(WIDTH-1,c+dc));
                window[n++]=input[rr*WIDTH+cc]&255;
            }
            java.util.Arrays.sort(window);output[r*WIDTH+c]=(byte)window[4];
        }
        return output;
    }
    static int[] sourceHeightEdges(byte[] input) {
        int[] output=new int[input.length];int[] smooth={1,2,1};
        for(int r=0;r<HEIGHT;r++)for(int c=0;c<WIDTH;c++) {
            int gx=0,gy=0;
            for(int dr=-1;dr<=1;dr++)for(int dc=-1;dc<=1;dc++) {
                int rr=reflect(r+dr,HEIGHT),cc=reflect(c+dc,WIDTH),value=input[rr*WIDTH+cc]&255;
                gx+=value*dc*smooth[dr+1];gy+=value*dr*smooth[dc+1];
            }
            int value=Math.max(Math.abs(gx),Math.abs(gy));output[r*WIDTH+c]=value>=50&&value<=120?100:0;
        }
        return output;
    }
    private static int reflect(int i,int length){return i<0?-i:i>=length?2*length-2-i:i;}

    static Grid octree2localprmapHeightEgo(Context context, float[] leaves,
                                           float locX, float locY, float locZ, float yaw) throws Exception {
        return octree2localprmapHeightEgo(context, leaves, locX, locY, locZ, yaw, false);
    }

    static Grid octree2localprmapHeightEgo(Context context, float[] leaves,
                                           float locX, float locY, float locZ, float yaw,
                                           boolean normalizePidNetWalkableCosts) throws Exception {
        Map<Integer, Float> config = loadConfig(context);
        int[] cost = new int[WIDTH * HEIGHT];
        byte[] height = new byte[WIDTH * HEIGHT];
        java.util.Arrays.fill(cost, -1); java.util.Arrays.fill(height, (byte) -1);
        float radius = 7.5f, ox = -radius - RESOLUTION * 2f, oy = -radius - RESOLUTION * 2f;
        float c = (float) Math.cos(yaw), s = (float) Math.sin(yaw);
        float[] sums = new float[cost.length]; int[] counts = new int[cost.length];
        boolean[] hardSemantic = new boolean[cost.length];
        float[] maxHeight = new float[cost.length]; java.util.Arrays.fill(maxHeight, -4.04f);
        GroundPlaneCostFilter groundFilter = new GroundPlaneCostFilter(WIDTH, HEIGHT);
        for (int n = 0; n + 7 < leaves.length; n += 8) {
            if (leaves[n + 3] < 0.5f) continue;
            float x=leaves[n], y=leaves[n+1], z=leaves[n+2];
            if (x < locX-radius || x > locX+radius || y < locY-radius || y > locY+radius || z < locZ-2.5f || z > locZ) continue;
            // Source map_transform.py reads OctoMap Color as (b, g, r) before
            // looking it up in the Mapillary planning configuration.
            int rgb=sourceLookupColor((int)leaves[n+4], (int)leaves[n+5], (int)leaves[n+6]); Float degree=config.get(rgb); if (degree==null) continue;
            if (normalizePidNetWalkableCosts) degree = pidNetNavigationDegree(rgb, degree);
            float dz=z-locZ; float metric = dz <= -0.4f && dz > -2.5f ? 1f : (dz <= -0.2f && dz > -0.4f ? 1f-(float)Math.pow((dz+0.4f)/0.2f,2) : 0f);
            float dx=x-locX, dy=y-locY; float ex=c*dx+s*dy, ey=-s*dx+c*dy;
            int gx=(int)((ex-ox)/RESOLUTION), gy=(int)((ey-oy)/RESOLUTION); if(gx<0||gx>=WIDTH||gy<0||gy>=HEIGHT) continue;
            int i=gy*WIDTH+gx; sums[i]+=degree*metric; counts[i]++;
            if (degree >= HARD_SEMANTIC_DEGREE && metric > 0f) hardSemantic[i] = true;
            if(dz>maxHeight[i]) maxHeight[i]=dz;
            groundFilter.observe(i, dz);
        }
        int known=0;
        for(int i=0;i<cost.length;i++) if(counts[i]>0){cost[i]=collapseCost(sums[i],counts[i],hardSemantic[i]); float h=maxHeight[i]; if(h>0)h=0; height[i]=(byte)((h+4.04f)*101f/4.04f-1f); known++;}
        GroundPlaneCostFilter.Result ground = groundFilter.apply(cost);
        return new Grid(cost,height,known,ground.groundHeight,ground.clearedCells,
                ground.groundSupportCells, ground.positiveCostCells, ground.groundCandidateCells);
    }

    static int sourceLookupColor(int red, int green, int blue) {
        return (blue << 16) | (green << 8) | red;
    }

    static float pidNetNavigationDegree(int rgb, float configuredDegree) {
        // Cityscapes alternates between road and sidewalk on visually identical plazas.
        // They are equally traversable for this pedestrian planner, so that label jitter
        // must not create an artificial cost gradient.
        // PIDNet has one road class. Keep observed road preferred to unknown (50),
        // while retaining sidewalk as the lowest-cost walkable surface.
        if (rgb == MAPILLARY_ROAD_RGB) return 0.2f;
        if (rgb == MAPILLARY_SIDEWALK_RGB) return 0f;
        return configuredDegree;
    }

    static int collapseCost(float sum, int count, boolean hardSemantic) {
        if (count <= 0) return -1;
        // A vertical obstacle often shares an x/y column with a ground voxel. Averaging
        // the column would turn a building (100) into passable yellow. Preserve hard
        // semantics here; GroundPlaneCostFilter can still clear a horizontal false positive.
        if (hardSemantic) return 100;
        return Math.max(0, Math.min(100, (int) (sum / count * 100f)));
    }

    private static Map<Integer, Float> loadConfig(Context context) throws Exception {
        Map<Integer,Float> cached=degreeConfig; if(cached!=null)return cached; StringBuilder b=new StringBuilder();
        try(BufferedReader r=new BufferedReader(new InputStreamReader(context.getAssets().open("source_port/planning/dataconfig_mapillary_extend.json"), StandardCharsets.UTF_8))){String line;while((line=r.readLine())!=null)b.append(line);}
        JSONArray labels=new JSONObject(b.toString()).getJSONArray("labels"); Map<Integer,Float> m=new HashMap<>();
        for(int i=0;i<labels.length();i++){JSONObject l=labels.getJSONObject(i);JSONArray c=l.getJSONArray("color");int rgb=(c.getInt(0)<<16)|(c.getInt(1)<<8)|c.getInt(2);m.put(rgb,(float)l.getDouble("degree"));} degreeConfig=m; return m;
    }
}
