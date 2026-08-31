package com.elabrador.mobilenavigation;

import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** Port of the local_planner A* call and grid/world path conversion. */
final class LocalPlanner {
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
    private final Object stateLock = new Object();

    LocalPlanner() { this(System::nanoTime); }

    LocalPlanner(LongSupplier nanoTime) {
        this.nanoTime = nanoTime;
        previousPathStampNanos = nanoTime.getAsLong();
    }

    PathResult plan(int[][] sourceMap, float resolution, float originX, float originY,
                    float locationX, float locationY, float dirX, float dirY) {
        final List<float[]> retainedPath;
        final long planGeneration;
        synchronized (stateLock) {
            expirePreviousPathLocked();
            retainedPath = new ArrayList<>(previousPath);
            planGeneration = stateGeneration;
        }
        int[][] map = LocalPlannerGrid.preprocess(sourceMap);
        float locCol=(locationX-originX)/resolution, locRow=(locationY-originY)/resolution;
        LocalPlannerGrid.Target target;
        try { target=LocalPlannerGrid.boundaryTarget(map.length,map[0].length,locRow,locCol,dirY,dirX); }
        catch (IllegalArgumentException e) { return new PathResult(Collections.emptyList(),false); }
        int startRow=Math.max(0,Math.min(map.length-1,LocalPlannerGrid.pythonRound(locRow)));
        int startCol=Math.max(0,Math.min(map[0].length-1,LocalPlannerGrid.pythonRound(locCol)));
        // Port of local_planner's previous target_path distance-transform penalty.
        // It preserves the source planner's route stability term before A*.
        applyPreviousPathCost(map, resolution, originX, originY, retainedPath);
        Env env=new Env(); env.obsMapSet(map.length,map[0].length,map);
        int startCost=map[startRow][startCol], targetCost=map[target.row][target.col];
        AStar astar=new AStar(new int[]{target.row,target.col},new int[]{startRow,startCol},map,env.obstacles,AStar.Heuristic.EUCLIDEAN,3f);
        List<int[]> path=astar.searching(); if(path.isEmpty()){
            synchronized (stateLock) {
                if (planGeneration == stateGeneration) previousPath.clear();
            }
            return new PathResult(
                Collections.emptyList(),true,false,Float.NaN,false,startCost,targetCost,
                env.obstacles.size(),visualize(sourceMap, Collections.emptyList(),
                resolution, originX, originY),null);
        }
        List<float[]> world=new ArrayList<>();
        for(int i=0;i<path.size()-1;i++){int[] p=path.get(i);world.add(new float[]{p[1]*resolution+originX,p[0]*resolution+originY});}
        Steering steering = purePursuitWithObstacleAvoidance(
                world, sourceMap, resolution, originX, originY,
                locationX, locationY, 0f, 1f, 2f);
        synchronized (stateLock) {
            if (planGeneration == stateGeneration) previousPath=world;
        }
        return new PathResult(world, true, true, steering.degrees, steering.blocked,
                startCost,targetCost,env.obstacles.size(),
                visualize(sourceMap, world, resolution, originX, originY),null);
    }
    List<float[]> previousPath(){
        synchronized (stateLock) {
            return Collections.unmodifiableList(new ArrayList<>(previousPath));
        }
    }
    void clearTargetPath() {
        synchronized (stateLock) {
            stateGeneration++;
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
            int col = LocalPlannerGrid.pythonRound((targetLocation[0] - originX) / resolution);
            int row = LocalPlannerGrid.pythonRound((targetLocation[1] - originY) / resolution);
            boolean inBounds = row >= 0 && row < rows && col >= 0 && col < cols;
            if (inBounds) visualization[row][col] = 127;
        }
        return visualization;
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
        int target = nearest;
        float currentAhead = aheadDistance;
        int previousTarget = -1;
        float previousAhead = Float.POSITIVE_INFINITY;
        for (int attempts = 0; attempts <= path.size(); attempts++) {
            target = nearest;
            int rasterAhead = (int)(currentAhead / resolution);
            if (path.size() - target > rasterAhead) target += rasterAhead;
            while (target + 1 < path.size()) {
                if (pathDistance[target + 1] >= currentAhead) break;
                target++;
            }
            if (target >= path.size()) target = path.size() - 1;
            Collision collision = checkCollision(
                    map, resolution, originX, originY,
                    locationX, locationY, path.get(target)[0], path.get(target)[1]);
            if (!collision.hit) break;
            if (collision.distanceMeters < 0.5f) return new Steering(Float.NaN, true);
            if (target == previousTarget
                    || collision.distanceMeters >= previousAhead - resolution * 0.25f) {
                return new Steering(Float.NaN, true);
            }
            previousTarget = target;
            previousAhead = collision.distanceMeters;
            currentAhead = collision.distanceMeters;
            if (attempts == path.size()) return new Steering(Float.NaN, true);
        }
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
        int x1=(int)((startX-originX)/resolution), y1=(int)((startY-originY)/resolution);
        int x2=(int)((endX-originX)/resolution), y2=(int)((endY-originY)/resolution);
        int dx=Math.abs(x2-x1), dy=Math.abs(y2-y1);
        int sx=x2-x1>0?1:-1, sy=y2-y1>0?1:-1;
        boolean interchange=dy>dx;
        if(interchange){int t=dx;dx=dy;dy=t;}
        int e=2*dy-dx, x=x1, y=y1;
        for(int i=0;i<dx+1;i++){
            if(y<0||y>=map.length||x<0||x>=map[0].length)return new Collision(false,-1f);
            int value=map[y][x];
            if(Env.isObstacleCost(value)){
                return new Collision(true,(float)Math.hypot(x-x1,y-y1)*resolution);
            }
            if(e>=0){if(interchange)x+=sx;else y+=sy;e-=2*dx;}
            if(interchange)y+=sy;else x+=sx;e+=2*dy;
        }
        return new Collision(false,-1f);
    }

    private static final class Steering { final float degrees; final boolean blocked; Steering(float d,boolean b){degrees=d;blocked=b;} }
    private static final class Collision { final boolean hit; final float distanceMeters; Collision(boolean h,float d){hit=h;distanceMeters=d;} }

    private void applyPreviousPathCost(int[][] map, float resolution, float originX, float originY,
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
            float best = Float.POSITIVE_INFINITY;
            for (int[] pathCell : pathCells) {
                float d = (float)Math.hypot(r - pathCell[0], c - pathCell[1]) * resolution;
                if (d < best) best = d;
            }
            if (Float.isFinite(best)) {
                int penalty = (int)(lambda * Math.min(best, capMeters) / capMeters);
                map[r][c] = Math.max(0, Math.min(255, map[r][c] + penalty));
            }
        }
    }
}
