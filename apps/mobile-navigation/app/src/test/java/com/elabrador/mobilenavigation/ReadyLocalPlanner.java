package com.elabrador.mobilenavigation;

import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** Port of the local_planner A* call and grid/world path conversion. */
final class ReadyLocalPlanner {
    private static final long PATH_LIFE_NANOS = TimeUnit.SECONDS.toNanos(30);
    static final class PathResult {
        final List<float[]> worldPath;
        final boolean planned;
        final boolean success;
        final float steeringDegrees;
        final boolean blocked;
        final int startCost;
        final int targetCost;
        final int obstacleCount;
        final int[][] visualizationGrid;
        final String waitingReason;
        PathResult(List<float[]> p, boolean s) {
            this(p, true, s, Float.NaN, false, -1, -1, 0, null, null);
        }
        PathResult(List<float[]> p, boolean s, float steeringDegrees, boolean blocked,
                   int startCost, int targetCost, int obstacleCount) {
            this(p, true, s, steeringDegrees, blocked, startCost, targetCost, obstacleCount,
                    null, null);
        }
        private PathResult(List<float[]> p, boolean planned, boolean s, float steeringDegrees,
                           boolean blocked, int startCost, int targetCost, int obstacleCount,
                           int[][] visualizationGrid, String waitingReason) {
            worldPath=p; this.planned=planned; success=s;
            this.steeringDegrees=steeringDegrees; this.blocked=blocked;
            this.startCost=startCost; this.targetCost=targetCost; this.obstacleCount=obstacleCount;
            this.visualizationGrid=visualizationGrid;
            this.waitingReason=waitingReason;
        }
        static PathResult waitingForTarget() {
            return waiting("等待导航目标方向");
        }
        static PathResult waiting(String reason) {
            return new PathResult(Collections.emptyList(), false, false,
                    Float.NaN, false, -1, -1, 0, null, reason);
        }
    }
    private List<float[]> previousPath = new ArrayList<>();
    private final LongSupplier nanoTime;
    private long previousPathStampNanos;
    private long stateGeneration;
    private VinsMono.Pose previousMapPose;
    private final Object stateLock = new Object();

    ReadyLocalPlanner() { this(System::nanoTime); }

    ReadyLocalPlanner(LongSupplier nanoTime) {
        this.nanoTime = nanoTime;
        previousPathStampNanos = nanoTime.getAsLong();
    }

    PathResult plan(int[][] sourceMap, float resolution, float originX, float originY,
                    float locationX, float locationY, float dirX, float dirY) {
        return plan(sourceMap, resolution, originX, originY, locationX, locationY, dirX, dirY, null);
    }

    PathResult plan(int[][] sourceMap, float resolution, float originX, float originY,
                    float locationX, float locationY, float dirX, float dirY,
                    VinsMono.Pose mapPose) {
        final List<float[]> retainedPath;
        final long planGeneration;
        synchronized (stateLock) {
            expirePreviousPathLocked();
            retainedPath = reprojectPath(previousPath, previousMapPose, mapPose);
            planGeneration = stateGeneration;
        }
        int[][] map = ReadyLocalPlannerGrid.preprocess(sourceMap);
        int[][] visualizationMap = planningVisualization(sourceMap, map);
        float locCol=(locationX-originX)/resolution, locRow=(locationY-originY)/resolution;
        ReadyLocalPlannerGrid.Target target;
        try { target=ReadyLocalPlannerGrid.boundaryTarget(map.length,map[0].length,locRow,locCol,dirY,dirX); }
        catch (IllegalArgumentException e) { return new PathResult(Collections.emptyList(),false); }
        int startRow=Math.max(0,Math.min(map.length-1,ReadyLocalPlannerGrid.pythonRound(locRow)));
        int startCol=Math.max(0,Math.min(map[0].length-1,ReadyLocalPlannerGrid.pythonRound(locCol)));
        // Port of local_planner's previous target_path distance-transform penalty.
        // It preserves the source planner's route stability term before A*.
        ReadyEnv env=new ReadyEnv(); env.obsMapSet(map.length,map[0].length,map);
        int[][] collisionMap = new int[map.length][];
        for (int r=0; r<map.length; r++) collisionMap[r] = map[r].clone();
        applyPreviousPathCost(map, resolution, originX, originY, retainedPath);
        int startCost=map[startRow][startCol], targetCost=map[target.row][target.col];
        ReadyAStar astar=new ReadyAStar(new int[]{target.row,target.col},new int[]{startRow,startCol},map,env.obstacles,ReadyAStar.Heuristic.EUCLIDEAN,3f);
        List<int[]> path=astar.searching(); if(path.isEmpty()){
            synchronized (stateLock) {
                if (planGeneration == stateGeneration) previousPath.clear();
            }
            return new PathResult(
                Collections.emptyList(),true,false,Float.NaN,false,startCost,targetCost,
                env.obstacles.size(),visualize(visualizationMap, Collections.emptyList(),
                resolution, originX, originY),null);
        }
        // This is a final safety invariant, independent of the renderer: never publish
        // a route containing a hard cell even if a future A* change regresses collision
        // handling. The cyan overlay otherwise hides the underlying cell color and can
        // make a rendering issue indistinguishable from a real unsafe route.
        for (int[] cell : path) {
            if (ReadyEnv.isObstacleCost(collisionMap[cell[0]][cell[1]])) {
                synchronized (stateLock) {
                    if (planGeneration == stateGeneration) previousPath.clear();
                }
                return new PathResult(
                        Collections.emptyList(), true, false, Float.NaN, true,
                        startCost, targetCost, env.obstacles.size(),
                        visualize(visualizationMap, Collections.emptyList(),
                                resolution, originX, originY), null);
            }
        }
        // Prefer a straight, fully collision-checked corridor when its soft cost is
        // essentially equivalent. Never cross a hard cell to straighten the path.
        List<int[]> straight = straightCells(startRow, startCol, target.row, target.col);
        if (safeCells(straight, collisionMap) && hasClearance(straight, collisionMap)
                && (lowCostCorridor(straight, collisionMap)
                || pathCost(straight, map) <= pathCost(path, map) * 1.10 + 3.0)) {
            path = straight;
        }
        List<float[]> world=new ArrayList<>();
        for(int i=0;i<path.size();i++){int[] p=path.get(i);world.add(new float[]{p[1]*resolution+originX,p[0]*resolution+originY});}
        Steering steering = purePursuitWithObstacleAvoidance(
                world, collisionMap, resolution, originX, originY,
                locationX, locationY, 0f, 1f, 2f);
        synchronized (stateLock) {
            if (planGeneration == stateGeneration) {
                previousPath=world;
                previousMapPose=mapPose;
            }
        }
        return new PathResult(world, true, true, steering.degrees, steering.blocked,
                startCost,targetCost,env.obstacles.size(),
                visualize(visualizationMap, world, resolution, originX, originY),null);
    }
    List<float[]> previousPath(){
        synchronized (stateLock) {
            return Collections.unmodifiableList(new ArrayList<>(previousPath));
        }
    }

    private static List<int[]> straightCells(int row, int col, int endRow, int endCol) {
        List<int[]> cells = new ArrayList<>();
        int dr=Math.abs(endRow-row), dc=Math.abs(endCol-col);
        int sr=Integer.signum(endRow-row), sc=Integer.signum(endCol-col), error=dc-dr;
        while (true) {
            cells.add(new int[]{row,col});
            if (row==endRow && col==endCol) return cells;
            int twice=2*error;
            if (twice > -dr) { error-=dr; col+=sc; }
            if (twice < dc) { error+=dc; row+=sr; }
        }
    }

    private static boolean safeCells(List<int[]> cells, int[][] map) {
        int[] previous=null;
        for (int[] cell:cells) {
            if (hardOrOutside(map,cell[0],cell[1])) return false;
            if (previous!=null && previous[0]!=cell[0] && previous[1]!=cell[1]
                    && (hardOrOutside(map,previous[0],cell[1])
                    || hardOrOutside(map,cell[0],previous[1]))) return false;
            previous=cell;
        }
        return true;
    }

    private static double pathCost(List<int[]> path, int[][] map) {
        double cost=0;
        for (int i=1;i<path.size();i++) {
            int[] a=path.get(i-1), b=path.get(i);
            cost+=3*Math.hypot(a[0]-b[0],a[1]-b[1])
                    + (Math.abs(map[a[0]][a[1]])+Math.abs(map[b[0]][b[1]]))*0.5;
        }
        return cost;
    }

    private static boolean lowCostCorridor(List<int[]> path, int[][] map) {
        for (int[] cell:path) if (map[cell[0]][cell[1]] > ReadyLocalPlannerGrid.SAFETY_COST) return false;
        return true;
    }

    private static boolean hasClearance(List<int[]> path, int[][] map) {
        for(int[] cell:path) for(int dr=-1;dr<=1;dr++) for(int dc=-1;dc<=1;dc++) {
            int row=cell[0]+dr, col=cell[1]+dc;
            if(row>=0 && row<map.length && col>=0 && col<map[0].length
                    && ReadyEnv.isObstacleCost(map[row][col])) return false;
        }
        return true;
    }
    void clearTargetPath() {
        synchronized (stateLock) {
            stateGeneration++;
            previousMapPose = null;
            previousPath.clear();
            previousPathStampNanos = nanoTime.getAsLong();
        }
    }

    /** Port of update_time_stamp(PATH_LIFE=30): periodically discard route-retention state. */
    private void expirePreviousPathLocked() {
        long now = nanoTime.getAsLong();
        if (now - previousPathStampNanos > PATH_LIFE_NANOS) {
            previousPath.clear();
            previousPathStampNanos = now;
        }
    }

    /** Port of local_planner.py visualize() followed by draw_target_path(). */
    static int[][] visualize(int[][] sourceMap, List<float[]> targetPath,
                             float resolution, float originX, float originY) {
        int rows = sourceMap.length, cols = sourceMap[0].length;
        int[][] visualization = new int[rows][cols];
        for (int row = 0; row < rows; row++) {
            System.arraycopy(sourceMap[row], 0, visualization[row], 0, cols);
        }
        for (float[] targetLocation : targetPath) {
            int col = ReadyLocalPlannerGrid.pythonRound((targetLocation[0] - originX) / resolution);
            int row = ReadyLocalPlannerGrid.pythonRound((targetLocation[1] - originY) / resolution);
            boolean inBounds = row >= 0 && row < rows && col >= 0 && col < cols;
            if (inBounds) visualization[row][col] = 127;
        }
        return visualization;
    }

    private static int[][] planningVisualization(int[][] sourceMap, int[][] planningMap) {
        int rows = sourceMap.length, cols = sourceMap[0].length;
        int[][] visible = new int[rows][cols];
        for (int row = 0; row < rows; row++) for (int col = 0; col < cols; col++) {
            int planned = planningMap[row][col];
            // Preserve distant unknown space as gray, but show safety inflation that
            // deliberately extends into an unknown glass/depth hole.
            visible[row][col] = sourceMap[row][col] < 0 && planned == 50 ? -1 : planned;
        }
        return visible;
    }

    /** Direct port of local_planner.py pure_pursuit_with_obstacle_avoidance(). */
    private Steering purePursuitWithObstacleAvoidance(
            List<float[]> path, int[][] map, float resolution, float originX, float originY,
            float locationX, float locationY, float directionX, float directionY,
            float aheadDistance) {
        if (path.isEmpty()) return new Steering(Float.NaN, true);
        int nearest = 0;
        float nearestDistance = Float.POSITIVE_INFINITY;
        float[] pathDistance = new float[path.size()];
        for (int i = 0; i < path.size(); i++) {
            float dx = locationX - path.get(i)[0];
            float dy = locationY - path.get(i)[1];
            float distance = dx * dx + dy * dy;
            pathDistance[i] = distance;
            if (distance < nearestDistance) { nearestDistance = distance; nearest = i; }
        }
        // Select by metric arc length, not waypoint count or squared/radial
        // distance. Check the actual line of travel using the same grid as A*.
        int target = -1;
        float along = 0f;
        for (int i = nearest; i < path.size(); i++) {
            if (i > nearest) along += (float)Math.hypot(
                    path.get(i)[0]-path.get(i-1)[0], path.get(i)[1]-path.get(i-1)[1]);
            float distance = (float)Math.sqrt(pathDistance[i]);
            if (distance >= 0.5f || i == path.size()-1) {
                Collision hit = checkCollision(map, resolution, originX, originY,
                        locationX, locationY, path.get(i)[0], path.get(i)[1]);
                if (hit.hit) break;
                target = i;
            }
            if (along >= aheadDistance) break;
        }
        if (target < 0) return new Steering(Float.NaN, true);
        float nextX = path.get(target)[0] - locationX;
        float nextY = path.get(target)[1] - locationY;
        float directionLength = (float)Math.hypot(directionX, directionY);
        float nextLength = (float)Math.hypot(nextX, nextY);
        if (directionLength < 1e-6f || nextLength < 1e-6f) return new Steering(0f, false);
        directionX /= directionLength; directionY /= directionLength;
        nextX /= nextLength; nextY /= nextLength;
        float cross = directionX * nextY - directionY * nextX;
        float dot = directionX * nextX + directionY * nextY;
        return new Steering((float)Math.toDegrees(Math.atan2(cross, dot)), false);
    }

    /** Direct port of map_draw.py bresenham_line_search/check_collision. */
    private Collision checkCollision(int[][] map, float resolution, float originX, float originY,
                                     float startX, float startY, float endX, float endY) {
        int x1=ReadyLocalPlannerGrid.pythonRound((startX-originX)/resolution);
        int y1=ReadyLocalPlannerGrid.pythonRound((startY-originY)/resolution);
        int x2=ReadyLocalPlannerGrid.pythonRound((endX-originX)/resolution);
        int y2=ReadyLocalPlannerGrid.pythonRound((endY-originY)/resolution);
        int nx=Math.abs(x2-x1), ny=Math.abs(y2-y1);
        int sx=Integer.signum(x2-x1), sy=Integer.signum(y2-y1);
        int x=x1, y=y1, ix=0, iy=0;
        while (true) {
            if (hardOrOutside(map,y,x))
                return new Collision(true,(float)Math.hypot(x-x1,y-y1)*resolution);
            if (ix==nx && iy==ny) break;
            long decision=(1L+2L*ix)*ny-(1L+2L*iy)*nx;
            if (decision==0) {
                if (hardOrOutside(map,y,x+sx) || hardOrOutside(map,y+sy,x))
                    return new Collision(true,(float)Math.hypot(x-x1,y-y1)*resolution);
                x+=sx; y+=sy; ix++; iy++;
            } else if (decision<0) { x+=sx; ix++; }
            else { y+=sy; iy++; }
        }
        return new Collision(false,-1f);
    }

    private static boolean hardOrOutside(int[][] map, int row, int col) {
        return row<0 || row>=map.length || col<0 || col>=map[0].length
                || ReadyEnv.isObstacleCost(map[row][col]);
    }

    /** Transform retained path from its original ego frame through VINS world to the new ego frame. */
    static List<float[]> reprojectPath(List<float[]> path, VinsMono.Pose oldPose, VinsMono.Pose newPose) {
        if (oldPose == null && newPose == null) return new ArrayList<>(path);
        if (oldPose == null || newPose == null || !oldPose.initialized || !newPose.initialized)
            return Collections.emptyList();
        double a=oldPose.egoRightAxisYawRadians(), b=newPose.egoRightAxisYawRadians();
        double ca=Math.cos(a), sa=Math.sin(a), cb=Math.cos(b), sb=Math.sin(b);
        List<float[]> result=new ArrayList<>();
        for (float[] point:path) {
            double dx=ca*point[0]-sa*point[1]+oldPose.x-newPose.x;
            double dy=sa*point[0]+ca*point[1]+oldPose.y-newPose.y;
            result.add(new float[]{(float)(cb*dx+sb*dy),(float)(-sb*dx+cb*dy)});
        }
        return result;
    }

    private static final class Steering { final float degrees; final boolean blocked; Steering(float d,boolean b){degrees=d;blocked=b;} }
    private static final class Collision { final boolean hit; final float distanceMeters; Collision(boolean h,float d){hit=h;distanceMeters=d;} }

    static void applyPreviousPathCost(int[][] map, float resolution, float originX, float originY,
                                      List<float[]> retainedPath) {
        if (retainedPath.isEmpty()) return;
        int rows = map.length, cols = map[0].length;
        List<int[]> pathCells = new ArrayList<>();
        boolean[][] pathMask = new boolean[rows][cols];
        for (float[] p : retainedPath) {
            int col = (int)((p[0] - originX) / resolution);
            int row = (int)((p[1] - originY) / resolution);
            if (row >= 0 && row < rows && col >= 0 && col < cols && !pathMask[row][col]) {
                pathMask[row][col] = true;
                pathCells.add(new int[]{row, col});
            }
        }
        final float lambda = 10f, capMeters = 2f;
        for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) {
            int bestSquared = Integer.MAX_VALUE;
            for (int[] pathCell : pathCells) {
                int dr = r - pathCell[0], dc = c - pathCell[1];
                int squared = dr * dr + dc * dc;
                if (squared < bestSquared) bestSquared = squared;
            }
            if (bestSquared != Integer.MAX_VALUE) {
                float best = (float)Math.sqrt(bestSquared) * resolution;
                int penalty = (int)(lambda * Math.min(best, capMeters) / capMeters);
                map[r][c] = Math.max(0, Math.min(255, map[r][c] + penalty));
            }
        }
    }
}
