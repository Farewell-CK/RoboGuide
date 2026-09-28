package com.elabrador.mobilenavigation;

import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** Port of the local_planner A* call and grid/world path conversion. */
final class BeforeLocalPlanner {
    private static final long PATH_LIFE_NANOS = TimeUnit.SECONDS.toNanos(30);
    private static final float COMMITTED_SAFETY_HORIZON_METERS = 3f;
    private static final float MIN_REMAINING_PATH_METERS = 2.5f;
    private static final float TARGET_CHANGE_DEGREES = 30f;
    private static final double MAX_LENGTH_RATIO_FOR_FEWER_TURNS = 1.25;
    private static final double[] TURN_SEARCH_PENALTIES = {4.0, 12.0, 36.0, 108.0};
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
    private List<float[]> pendingPath = new ArrayList<>();
    private VinsMono.Pose pendingMapPose;
    private final Object stateLock = new Object();

    BeforeLocalPlanner() { this(System::nanoTime); }

    BeforeLocalPlanner(LongSupplier nanoTime) {
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
        final List<float[]> retainedPendingPath;
        final long planGeneration;
        synchronized (stateLock) {
            expirePreviousPathLocked();
            retainedPath = reprojectPath(previousPath, previousMapPose, mapPose);
            retainedPendingPath = reprojectPath(pendingPath, pendingMapPose, mapPose);
            planGeneration = stateGeneration;
        }
        int[][] map = BeforeLocalPlannerGrid.preprocess(sourceMap);
        int[][] visualizationMap = planningVisualization(sourceMap, map);
        float locCol=(locationX-originX)/resolution, locRow=(locationY-originY)/resolution;
        BeforeLocalPlannerGrid.Target target;
        try { target=BeforeLocalPlannerGrid.boundaryTarget(map.length,map[0].length,locRow,locCol,dirY,dirX); }
        catch (IllegalArgumentException e) { return new PathResult(Collections.emptyList(),false); }
        int startRow=Math.max(0,Math.min(map.length-1,BeforeLocalPlannerGrid.pythonRound(locRow)));
        int startCol=Math.max(0,Math.min(map[0].length-1,BeforeLocalPlannerGrid.pythonRound(locCol)));
        // Port of local_planner's previous target_path distance-transform penalty.
        // It preserves the source planner's route stability term before A*.
        BeforeEnv env=new BeforeEnv(); env.obsMapSet(map.length,map[0].length,map);
        int[][] collisionMap = new int[map.length][];
        for (int r=0; r<map.length; r++) collisionMap[r] = map[r].clone();
        applyPreviousPathCost(map, resolution, originX, originY, retainedPath);
        int startCost=map[startRow][startCol], targetCost=map[target.row][target.col];
        BeforeAStar astar=new BeforeAStar(new int[]{target.row,target.col},new int[]{startRow,startCol},map,env.obstacles,BeforeAStar.Heuristic.EUCLIDEAN,3f);
        List<int[]> path=astar.searching();
        List<int[]> shortestPath = new ArrayList<>(path);
        // This is a final safety invariant, independent of the renderer: never publish
        // a route containing a hard cell even if a future A* change regresses collision
        // handling. The cyan overlay otherwise hides the underlying cell color and can
        // make a rendering issue indistinguishable from a real unsafe route.
        for (int[] cell : path) {
            if (BeforeEnv.isObstacleCost(collisionMap[cell[0]][cell[1]])) {
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
        if (!path.isEmpty()) {
            List<int[]> straight = straightCells(startRow, startCol, target.row, target.col);
            if (safeCells(straight, collisionMap) && hasClearance(straight, collisionMap)
                    && (lowCostCorridor(straight, collisionMap)
                    || pathCost(straight, map) <= pathCost(path, map) * 1.10 + 3.0)) {
                path = straight;
            } else {
                path = simplifyPath(path, collisionMap, map);
            }
        }
        List<float[]> world=new ArrayList<>();
        for(int i=0;i<path.size();i++){int[] p=path.get(i);world.add(new float[]{p[1]*resolution+originX,p[0]*resolution+originY});}
        List<float[]> committed = trimBeforeNearest(retainedPath, locationX, locationY);
        float committedObstacleDistance = firstHardObstacleDistance(committed, collisionMap,
                resolution, originX, originY, locationX, locationY);
        boolean holdCommitted = !committed.isEmpty()
                && remainingLength(committed) >= MIN_REMAINING_PATH_METERS
                && targetDifferenceDegrees(committed, locationX, locationY, dirX, dirY)
                        < TARGET_CHANGE_DEGREES
                && committedObstacleDistance >= COMMITTED_SAFETY_HORIZON_METERS;
        if (holdCommitted) {
            Steering retainedSteering = purePursuitWithObstacleAvoidance(
                    committed, collisionMap, resolution, originX, originY,
                    locationX, locationY, 0f, 1f, 2f);
            // A hard obstacle inside pure-pursuit reach is never held, regardless of
            // numerical distance at the three-metre boundary.
            if (!retainedSteering.blocked) {
                List<float[]> backgroundCandidate = world;
                // Only spend the extra searches when a distant obstacle has actually
                // invalidated the committed route's far section.
                if (!path.isEmpty() && Float.isFinite(committedObstacleDistance)) {
                    path = chooseFewestTurnPath(shortestPath, path, map,
                            collisionMap, env.obstacles,
                            new int[]{target.row,target.col},
                            new int[]{startRow,startCol});
                    backgroundCandidate = gridPathToWorld(
                            path, resolution, originX, originY);
                }
                synchronized (stateLock) {
                    if (planGeneration == stateGeneration) {
                        // Keep the newest full-map candidate ready, but do not expose it
                        // while the committed three-metre corridor remains safe.
                        if (!backgroundCandidate.isEmpty()) {
                            pendingPath = backgroundCandidate;
                            pendingMapPose = mapPose;
                        }
                        previousPath = committed;
                        previousMapPose = mapPose;
                        previousPathStampNanos = nanoTime.getAsLong();
                    }
                }
                return new PathResult(committed, true, true, retainedSteering.degrees, false,
                        startCost, targetCost, env.obstacles.size(),
                        visualize(visualizationMap, committed, resolution, originX, originY), null);
            }
        }
        if (!world.isEmpty()) {
            path = chooseFewestTurnPath(shortestPath, path, map, collisionMap,
                    env.obstacles, new int[]{target.row,target.col},
                    new int[]{startRow,startCol});
            world = gridPathToWorld(path, resolution, originX, originY);
        }
        if (world.isEmpty()) {
            boolean pendingSafe = !retainedPendingPath.isEmpty()
                    && remainingLength(retainedPendingPath) >= MIN_REMAINING_PATH_METERS
                    && targetDifferenceDegrees(retainedPendingPath, locationX, locationY,
                            dirX, dirY) < TARGET_CHANGE_DEGREES
                    && Float.isInfinite(firstHardObstacleDistance(retainedPendingPath,
                            collisionMap, resolution, originX, originY, locationX, locationY));
            if (pendingSafe) world = retainedPendingPath;
        }
        if (world.isEmpty()) {
            synchronized (stateLock) {
                if (planGeneration == stateGeneration) previousPath.clear();
            }
            return new PathResult(
                    Collections.emptyList(), true, false, Float.NaN, false,
                    startCost, targetCost, env.obstacles.size(),
                    visualize(visualizationMap, Collections.emptyList(),
                            resolution, originX, originY), null);
        }
        Steering steering = purePursuitWithObstacleAvoidance(
                world, collisionMap, resolution, originX, originY,
                locationX, locationY, 0f, 1f, 2f);
        synchronized (stateLock) {
            if (planGeneration == stateGeneration) {
                previousPath=world;
                previousMapPose=mapPose;
                pendingPath.clear();
                pendingMapPose = null;
                previousPathStampNanos = nanoTime.getAsLong();
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

    /** Greedily removes avoidable bends, while preserving collision and soft-cost intent. */
    static List<int[]> simplifyPath(List<int[]> path, int[][] collisionMap, int[][] costMap) {
        return rasterizeAnchors(simplificationAnchors(path, collisionMap, costMap));
    }

    private static List<int[]> simplificationAnchors(List<int[]> path,
                                                      int[][] collisionMap, int[][] costMap) {
        if (path.size() < 3) return new ArrayList<>(path);
        List<int[]> result = new ArrayList<>();
        int anchor = 0;
        result.add(path.get(0));
        while (anchor < path.size() - 1) {
            int chosen = anchor + 1;
            for (int candidate = path.size() - 1; candidate > anchor + 1; candidate--) {
                List<int[]> direct = straightCells(path.get(anchor)[0], path.get(anchor)[1],
                        path.get(candidate)[0], path.get(candidate)[1]);
                if (!safeCells(direct, collisionMap)) continue;
                List<int[]> original = path.subList(anchor, candidate + 1);
                if (pathCost(direct, costMap) <= pathCost(original, costMap) * 1.15 + 3.0) {
                    chosen = candidate;
                    break;
                }
            }
            result.add(path.get(chosen));
            anchor = chosen;
        }
        return result;
    }

    private static List<int[]> rasterizeAnchors(List<int[]> anchors) {
        if (anchors.size() < 2) return new ArrayList<>(anchors);
        List<int[]> result = new ArrayList<>();
        result.add(anchors.get(0));
        for (int anchor=1; anchor<anchors.size(); anchor++) {
            int[] a=anchors.get(anchor-1),b=anchors.get(anchor);
            List<int[]> segment=straightCells(a[0],a[1],b[0],b[1]);
            for(int i=1;i<segment.size();i++)result.add(segment.get(i));
        }
        return result;
    }

    private static List<int[]> chooseFewestTurnPath(List<int[]> shortestPath,
                                                     List<int[]> initialCandidate,
                                                     int[][] costMap, int[][] collisionMap,
                                                     Set<Long> obstacles,
                                                     int[] searchStart, int[] searchGoal) {
        if (shortestPath.isEmpty()) return initialCandidate;
        double maximumLength=gridPathLength(shortestPath)*MAX_LENGTH_RATIO_FOR_FEWER_TURNS;
        List<int[]> best=simplifyPath(initialCandidate,collisionMap,costMap);
        int bestTurns=meaningfulTurnCount(best,collisionMap,costMap);
        double bestLength=gridPathLength(best),bestCost=pathCost(best,costMap);
        for(double penalty:TURN_SEARCH_PENALTIES) {
            List<int[]> raw=new BeforeAStar(searchStart,searchGoal,costMap,obstacles,
                    BeforeAStar.Heuristic.EUCLIDEAN,3f,penalty).searching();
            if(raw.isEmpty())continue;
            List<int[]> candidate=simplifyPath(raw,collisionMap,costMap);
            double length=gridPathLength(candidate);
            if(length>maximumLength+1e-6)continue;
            int turns=meaningfulTurnCount(candidate,collisionMap,costMap);
            double candidateCost=pathCost(candidate,costMap);
            if(turns<bestTurns || (turns==bestTurns
                    && (length<bestLength-1e-6
                    || (Math.abs(length-bestLength)<=1e-6 && candidateCost<bestCost)))) {
                best=candidate;bestTurns=turns;bestLength=length;bestCost=candidateCost;
            }
        }
        return best;
    }

    private static int meaningfulTurnCount(List<int[]> path, int[][] collisionMap,
                                           int[][] costMap) {
        return Math.max(0,simplificationAnchors(path,collisionMap,costMap).size()-2);
    }

    private static double gridPathLength(List<int[]> path) {
        double length=0.0;
        for(int i=1;i<path.size();i++)length+=Math.hypot(
                path.get(i)[0]-path.get(i-1)[0],path.get(i)[1]-path.get(i-1)[1]);
        return length;
    }

    private static List<float[]> gridPathToWorld(List<int[]> path, float resolution,
                                                  float originX, float originY) {
        List<float[]> world=new ArrayList<>();
        for(int[] point:path)world.add(new float[]{point[1]*resolution+originX,
                point[0]*resolution+originY});
        return world;
    }

    private static List<float[]> trimBeforeNearest(List<float[]> path, float x, float y) {
        if (path.isEmpty()) return Collections.emptyList();
        int nearest = 0;
        double best = Double.POSITIVE_INFINITY;
        for (int i = 0; i < path.size(); i++) {
            double d = Math.hypot(path.get(i)[0] - x, path.get(i)[1] - y);
            if (d < best) { best = d; nearest = i; }
        }
        return new ArrayList<>(path.subList(nearest, path.size()));
    }

    private static float remainingLength(List<float[]> path) {
        float length = 0f;
        for (int i = 1; i < path.size(); i++) length += (float)Math.hypot(
                path.get(i)[0] - path.get(i-1)[0], path.get(i)[1] - path.get(i-1)[1]);
        return length;
    }

    private static float targetDifferenceDegrees(List<float[]> path, float x, float y,
                                                  float dirX, float dirY) {
        if (path.isEmpty()) return Float.POSITIVE_INFINITY;
        float[] end = path.get(path.size()-1);
        double oldX = end[0] - x, oldY = end[1] - y;
        double oldLength = Math.hypot(oldX, oldY), newLength = Math.hypot(dirX, dirY);
        if (oldLength < 1e-6 || newLength < 1e-6) return Float.POSITIVE_INFINITY;
        double dot = (oldX * dirX + oldY * dirY) / (oldLength * newLength);
        return (float)Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, dot))));
    }

    private static float firstHardObstacleDistance(List<float[]> path, int[][] map,
                                                    float resolution, float originX,
                                                    float originY, float x, float y) {
        if (path.isEmpty()) return 0f;
        float traversed = 0f;
        float fromX = x, fromY = y;
        for (float[] point : path) {
            float segment = (float)Math.hypot(point[0] - fromX, point[1] - fromY);
            List<int[]> cells = straightCells(
                    BeforeLocalPlannerGrid.pythonRound((fromY-originY)/resolution),
                    BeforeLocalPlannerGrid.pythonRound((fromX-originX)/resolution),
                    BeforeLocalPlannerGrid.pythonRound((point[1]-originY)/resolution),
                    BeforeLocalPlannerGrid.pythonRound((point[0]-originX)/resolution));
            for (int i = 0; i < cells.size(); i++) {
                int[] cell = cells.get(i);
                if (hardOrOutside(map, cell[0], cell[1])) {
                    return traversed + segment * i / Math.max(1, cells.size()-1);
                }
            }
            traversed += segment;
            fromX = point[0]; fromY = point[1];
        }
        return Float.POSITIVE_INFINITY;
    }

    private static boolean lowCostCorridor(List<int[]> path, int[][] map) {
        for (int[] cell:path) if (map[cell[0]][cell[1]] > BeforeLocalPlannerGrid.SAFETY_COST) return false;
        return true;
    }

    private static boolean hasClearance(List<int[]> path, int[][] map) {
        for(int[] cell:path) for(int dr=-1;dr<=1;dr++) for(int dc=-1;dc<=1;dc++) {
            int row=cell[0]+dr, col=cell[1]+dc;
            if(row>=0 && row<map.length && col>=0 && col<map[0].length
                    && BeforeEnv.isObstacleCost(map[row][col])) return false;
        }
        return true;
    }
    void clearTargetPath() {
        synchronized (stateLock) {
            stateGeneration++;
            previousMapPose = null;
            previousPath.clear();
            pendingMapPose = null;
            pendingPath.clear();
            previousPathStampNanos = nanoTime.getAsLong();
        }
    }

    /** Port of update_time_stamp(PATH_LIFE=30): periodically discard route-retention state. */
    private void expirePreviousPathLocked() {
        long now = nanoTime.getAsLong();
        if (now - previousPathStampNanos > PATH_LIFE_NANOS) {
            previousPath.clear();
            pendingPath.clear();
            pendingMapPose = null;
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
            int col = BeforeLocalPlannerGrid.pythonRound((targetLocation[0] - originX) / resolution);
            int row = BeforeLocalPlannerGrid.pythonRound((targetLocation[1] - originY) / resolution);
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
        int x1=BeforeLocalPlannerGrid.pythonRound((startX-originX)/resolution);
        int y1=BeforeLocalPlannerGrid.pythonRound((startY-originY)/resolution);
        int x2=BeforeLocalPlannerGrid.pythonRound((endX-originX)/resolution);
        int y2=BeforeLocalPlannerGrid.pythonRound((endY-originY)/resolution);
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
                || BeforeEnv.isObstacleCost(map[row][col]);
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
