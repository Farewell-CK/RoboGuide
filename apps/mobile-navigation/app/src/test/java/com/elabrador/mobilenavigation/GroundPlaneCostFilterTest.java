package com.elabrador.mobilenavigation;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;

public class GroundPlaneCostFilterTest {
    private static final int WIDTH = 20;
    private static final int HEIGHT = 20;

    @Test
    public void broadCarpetPlaneMisclassifiedAsBuildingBecomesTraversable() {
        GroundPlaneCostFilter filter = new GroundPlaneCostFilter(WIDTH, HEIGHT);
        int[] cost = filledCost(100);
        observePlane(filter, -1.20f);

        GroundPlaneCostFilter.Result result = filter.apply(cost);

        assertTrue(result.hasGround());
        assertEquals(100, result.clearedCells);
        assertEquals(0, cost[index(8, 8)]);
    }

    @Test
    public void cabinetAndPersonAboveCarpetRemainObstacles() {
        GroundPlaneCostFilter filter = new GroundPlaneCostFilter(WIDTH, HEIGHT);
        int[] cost = filledCost(100);
        observePlane(filter, -1.20f);
        for (int y = 7; y <= 9; y++) {
            for (int x = 7; x <= 9; x++) {
                filter.observe(index(x, y), -0.70f);
            }
        }

        GroundPlaneCostFilter.Result result = filter.apply(cost);

        assertTrue(result.hasGround());
        assertEquals(91, result.clearedCells);
        assertEquals(100, cost[index(8, 8)]);
        assertEquals(0, cost[index(4, 4)]);
    }

    @Test
    public void sparseOrNarrowEvidenceCannotEraseSourceSemanticCost() {
        GroundPlaneCostFilter filter = new GroundPlaneCostFilter(WIDTH, HEIGHT);
        int[] cost = filledCost(100);
        for (int y = 2; y < 15; y++) filter.observe(index(5, y), -1.20f);

        GroundPlaneCostFilter.Result result = filter.apply(cost);

        assertFalse(result.hasGround());
        assertEquals(0, result.clearedCells);
        assertEquals(100, cost[index(5, 8)]);
    }

    @Test
    public void lowObjectAboveFloorIsNotCleared() {
        GroundPlaneCostFilter filter = new GroundPlaneCostFilter(WIDTH, HEIGHT);
        int[] cost = filledCost(100);
        observePlane(filter, -1.20f);
        filter.observe(index(8, 8), -1.05f);

        filter.apply(cost);

        assertEquals(100, cost[index(8, 8)]);
    }

    @Test
    public void pidNetRoadAndSidewalkHaveTheSameWalkableCost() {
        assertEquals(0f, MapTransform.pidNetNavigationDegree(0x804080, 0.2f), 0f);
        assertEquals(0f, MapTransform.pidNetNavigationDegree(0xf423e8, 0f), 0f);
        assertEquals(0.6f, MapTransform.pidNetNavigationDegree(0x6b8e23, 0.6f), 0f);
    }

    @Test
    public void hardObstacleCannotBeDilutedByGroundInTheSameColumn() {
        assertEquals(100, MapTransform.collapseCost(1f, 2, true));
        assertEquals(50, MapTransform.collapseCost(1f, 2, false));
    }

    @Test
    public void currentFrameGroundCorrectionPreventsSemanticObstacleAccumulation() {
        int side = 6;
        int floorPoints = side * side;
        float[] xyz = new float[(floorPoints + 1) * 3];
        int[] colors = new int[floorPoints + 1];
        float[] confidence = new float[floorPoints + 1];
        Arrays.fill(colors, 0x464646);
        Arrays.fill(confidence, 0.04f);
        int point = 0;
        for (int y = 0; y < side; y++) {
            for (int x = 0; x < side; x++) {
                xyz[point * 3] = x * 0.2f;
                xyz[point * 3 + 1] = y * 0.2f;
                xyz[point * 3 + 2] = -1.2f;
                point++;
            }
        }
        // A low object shares one floor cell and must protect that whole column.
        xyz[point * 3] = 0.4f;
        xyz[point * 3 + 1] = 0.4f;
        xyz[point * 3 + 2] = -0.7f;
        SemanticPointCloud.Data cloud = new SemanticPointCloud.Data(xyz, colors, confidence);

        FrameGroundSemanticFilter.Result result = FrameGroundSemanticFilter.apply(
                cloud, new float[]{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1},
                0xe823f4);

        assertTrue(Float.isFinite(result.groundHeight));
        assertTrue(result.correctedPoints >= floorPoints - 2);
        assertEquals(0xe823f4, colors[0]);
        assertEquals(0x464646, colors[2 * side + 2]);
        assertEquals(0x464646, colors[floorPoints]);
    }

    private static int[] filledCost(int value) {
        int[] cost = new int[WIDTH * HEIGHT];
        Arrays.fill(cost, -1);
        for (int y = 3; y < 13; y++) {
            for (int x = 3; x < 13; x++) cost[index(x, y)] = value;
        }
        return cost;
    }

    private static void observePlane(GroundPlaneCostFilter filter, float height) {
        for (int y = 3; y < 13; y++) {
            for (int x = 3; x < 13; x++) filter.observe(index(x, y), height);
        }
    }

    private static int index(int x, int y) {
        return y * WIDTH + x;
    }
}
