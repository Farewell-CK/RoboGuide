package com.elabrador.mobilenavigation;

import org.junit.Test;
import java.util.Random;
import static org.junit.Assert.*;

public class SparseGroundEquivalenceTest {
    @Test public void frameGroundMatchesDenseScanWithFloorAndVerticalObstacles() {
        Random random = new Random(91L);
        float[] transform = {1,0,0,0.31f, 0,1,0,-0.23f, 0,0,1,0.17f, 0,0,0,1};
        for (int scene = 0; scene < 50; scene++) {
            int count = scene * 97;
            float[] xyz = new float[count * 3];
            int[] colors = new int[count];
            float[] confidence = new float[count];
            for (int i = 0; i < count; i++) {
                xyz[i * 3] = random.nextFloat() * 12f - 6f;
                xyz[i * 3 + 1] = random.nextFloat() * 12f - 6f;
                xyz[i * 3 + 2] = i % 5 == 0 ? random.nextFloat() * 3f - 3f
                        : -1.2f + random.nextFloat() * 0.08f;
                colors[i] = random.nextInt(0xffffff);
                confidence[i] = random.nextFloat();
            }
            SemanticPointCloud.Data actual = new SemanticPointCloud.Data(xyz, colors, confidence);
            SemanticPointCloud.Data reference = new SemanticPointCloud.Data(
                    xyz.clone(), colors.clone(), confidence.clone());
            FrameGroundSemanticFilter.Result a = FrameGroundSemanticFilter.apply(actual, transform, 17);
            DenseFrameGroundReference.Result b = DenseFrameGroundReference.apply(reference, transform, 17);
            assertArrayEquals(reference.semanticRgb, actual.semanticRgb);
            assertArrayEquals(reference.confidence, actual.confidence, 0f);
            assertEquals(b.groundHeight, a.groundHeight, 0f);
            assertEquals(b.supportCells, a.supportCells);
            assertEquals(b.correctedPoints, a.correctedPoints);
        }
    }
    @Test public void sparseAndDenseScansAgreeAcrossRandomScenes() {
        Random random = new Random(20260907L);
        for (int scene = 0; scene < 150; scene++) {
            int width = 20, height = 20, cells = width * height;
            GroundPlaneCostFilter sparse = new GroundPlaneCostFilter(width, height);
            DenseGroundReference dense = new DenseGroundReference(width, height);
            int[] cost = new int[cells];
            for (int cell = 0; cell < cells; cell++) cost[cell] = random.nextInt(102) - 1;
            for (int point = 0; point < scene * 13; point++) {
                int cell = random.nextInt(cells);
                float z = scene % 3 == 0 ? -1.2f + random.nextFloat() * 0.08f
                        : -3f + random.nextFloat() * 3.2f;
                sparse.observe(cell, z); dense.observe(cell, z);
            }
            int[] expected = cost.clone();
            GroundPlaneCostFilter.Result actual = sparse.apply(cost);
            DenseGroundReference.Result reference = dense.apply(expected);
            assertArrayEquals(expected, cost);
            assertEquals(reference.groundHeight, actual.groundHeight, 0f);
            assertEquals(reference.clearedCells, actual.clearedCells);
            assertEquals(reference.groundSupportCells, actual.groundSupportCells);
            assertEquals(reference.positiveCostCells, actual.positiveCostCells);
            assertEquals(reference.groundCandidateCells, actual.groundCandidateCells);
        }
    }
}
