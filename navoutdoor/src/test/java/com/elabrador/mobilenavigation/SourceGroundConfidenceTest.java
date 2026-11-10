package com.elabrador.mobilenavigation;

import java.util.Arrays;
import java.util.Random;
import org.junit.Test;
import static org.junit.Assert.*;

public class SourceGroundConfidenceTest {
    private static final int SIDEWALK = 0xe823f4;
    private static final int PEDESTRIAN = 0x606060;
    private static final int ZEBRA = 0xffffff;
    private static final int ROAD = 0x804080;
    private static final int BUILDING = 0x464646;
    private static final int[] WALKABLE = {SIDEWALK, PEDESTRIAN, ZEBRA};

    private static float[] cameraToWorld() {
        // Optical x=right, y=down, z=forward; world z points up.
        return new float[]{1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1};
    }

    private static SemanticPointCloud.Data floor(int extraPoints) {
        int points = 25 + extraPoints;
        float[] xyz = new float[points * 3];
        int[] colors = new int[points];
        float[] confidence = new float[points];
        Arrays.fill(colors, SIDEWALK);
        Arrays.fill(confidence, .3f);
        for (int row = 0; row < 5; row++) for (int col = 0; col < 5; col++) {
            int p = (row * 5 + col) * 3;
            xyz[p] = col;
            xyz[p + 1] = 0;
            xyz[p + 2] = row + 1;
        }
        return new SemanticPointCloud.Data(xyz, colors, confidence);
    }

    private static void point(SemanticPointCloud.Data cloud, int index, float x, float y, float z) {
        cloud.xyz[index * 3] = x;
        cloud.xyz[index * 3 + 1] = y;
        cloud.xyz[index * 3 + 2] = z;
    }

    /** Repeat one noncollinear floor triplet to test the exact distance gates. */
    private static final class FixedTriangle extends Random {
        int calls;
        @Override public int nextInt(int bound) {
            int value = new int[]{0,1,5}[calls++ % 3];
            assertTrue(value < bound);
            return value;
        }
    }

    @Test public void onlyAlreadyWalkableLabelsGainConfidenceAndGeometryStaysUntouched() {
        SemanticPointCloud.Data cloud = floor(0);
        for (int i = 0; i < cloud.semanticRgb.length; i++) {
            cloud.semanticRgb[i] = new int[]{SIDEWALK,PEDESTRIAN,ZEBRA,ROAD,BUILDING}[i % 5];
        }
        int[] originalColors = cloud.semanticRgb.clone();
        float[] originalXyz = cloud.xyz.clone();
        SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                cloud, cameraToWorld(), WALKABLE, new Random(912908));
        assertTrue(result.planeValid);
        assertEquals(25, result.inliers);
        assertEquals(15, result.boostedPoints);
        assertArrayEquals(originalColors, cloud.semanticRgb);
        assertArrayEquals(originalXyz, cloud.xyz, 0f);
        for (int i = 0; i < cloud.confidence.length; i++) {
            assertEquals(i % 5 < 3 ? 3f : .3f, cloud.confidence[i], 1e-6f);
        }
    }

    @Test public void wallCannotBecomeGroundEvenWhenEveryLabelIsWalkable() {
        SemanticPointCloud.Data cloud = floor(0);
        for (int i = 0; i < cloud.semanticRgb.length; i++) {
            cloud.xyz[i * 3 + 1] = cloud.xyz[i * 3 + 2];
            cloud.xyz[i * 3 + 2] = 2;
        }
        float[] before = cloud.confidence.clone();
        SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                cloud, cameraToWorld(), WALKABLE, new Random(42));
        assertFalse(result.planeValid);
        assertEquals(25, result.inliers);
        assertEquals(0, result.boostedPoints);
        assertArrayEquals(before, cloud.confidence, 0f);
    }

    @Test public void dominantWallIsRejectedInsteadOfChoosingAMinorHorizontalPlane() {
        SemanticPointCloud.Data cloud = floor(9);
        for (int i = 0; i < 25; i++) {
            cloud.xyz[i * 3 + 1] = cloud.xyz[i * 3 + 2];
            cloud.xyz[i * 3 + 2] = 2;
        }
        for (int row = 0; row < 3; row++) for (int col = 0; col < 3; col++) {
            point(cloud,25 + row * 3 + col,col,0,row + 3);
        }
        Random bothPlanes = new Random() {
            int calls;
            @Override public int nextInt(int bound) {
                int call = calls++;
                return call < 3 ? new int[]{0,1,5}[call] : new int[]{25,26,28}[(call - 3) % 3];
            }
        };
        SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                cloud, cameraToWorld(), WALKABLE, bothPlanes);
        assertFalse(result.planeValid);
        assertEquals(25, result.inliers);
        assertEquals(0, result.boostedPoints);
        for (float confidence : cloud.confidence) assertEquals(.3f,confidence,0f);
    }

    @Test public void nearbyRaisedPointsUseStrictTenCentimeterDistance() {
        SemanticPointCloud.Data cloud = floor(3);
        point(cloud,25,1,Math.nextDown(.1f),2);
        point(cloud,26,1,.1f,2);
        point(cloud,27,1,Math.nextUp(.1f),2);
        SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                cloud, cameraToWorld(), WALKABLE, new FixedTriangle());
        assertTrue(result.planeValid);
        assertEquals(26, result.inliers);
        assertEquals(26, result.boostedPoints);
        assertEquals(3f, cloud.confidence[25], 1e-6f);
        assertEquals(.3f, cloud.confidence[26], 0f);
        assertEquals(.3f, cloud.confidence[27], 0f);
    }

    @Test public void fitDepthLimitsDoNotTruncateTheResultingGroundMask() {
        SemanticPointCloud.Data cloud = floor(6);
        point(cloud,25,1,0,.1f);
        point(cloud,26,1,0,Math.nextUp(.1f));
        point(cloud,27,1,0,Math.nextDown(10f));
        point(cloud,28,1,0,10f);
        point(cloud,29,1,0,12f);
        point(cloud,30,Float.NaN,0,2f);
        SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                cloud, cameraToWorld(), WALKABLE, new FixedTriangle());
        assertEquals(27, result.candidatePoints);
        assertEquals(27, result.inliers);
        assertEquals(29, result.boostedPoints);
        assertEquals(.3f, cloud.confidence[25], 0f);
        assertEquals(3f, cloud.confidence[28], 1e-6f);
        assertEquals(3f, cloud.confidence[29], 1e-6f);
        assertEquals(.3f, cloud.confidence[30], 0f);
    }

    @Test public void translationDoesNotAffectGravityAndBoostIsNotClampedToOne() {
        SemanticPointCloud.Data cloud = floor(0);
        Arrays.fill(cloud.confidence, 2f);
        float[] transform = cameraToWorld();
        transform[3] = 100000;
        transform[7] = -500000;
        transform[11] = 200000;
        SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                cloud, transform, WALKABLE, new FixedTriangle());
        assertTrue(result.planeValid);
        assertEquals(25, result.boostedPoints);
        assertEquals(20f, cloud.confidence[0], 0f);
    }

    @Test public void worldGravityGateUsesNormalDirectionRatherThanCameraHeight() {
        for (float vertical : new float[]{.7099f,.7101f,-.7101f}) {
            SemanticPointCloud.Data cloud = floor(0);
            float c = (float)Math.sqrt(1 - vertical * vertical);
            float[] transform = {1,0,0,0, 0,c,-vertical,0, 0,vertical,c,0, 0,0,0,1};
            SourceGroundConfidence.Result result = SourceGroundConfidence.apply(
                    cloud, transform, WALKABLE, new FixedTriangle());
            assertEquals(Math.abs(vertical) > .71f, result.planeValid);
            assertEquals(result.planeValid ? 25 : 0, result.boostedPoints);
        }
    }

    @Test public void ransacRunsTwentyTrialsAndAllowsRepeatedPointIndices() {
        FixedTriangle random = new FixedTriangle();
        assertTrue(SourceGroundConfidence.apply(floor(0),cameraToWorld(),WALKABLE,random).planeValid);
        assertEquals(60, random.calls);
        Random samePoint = new Random() {
            @Override public int nextInt(int bound) { return 0; }
        };
        SourceGroundConfidence.Result degenerate = SourceGroundConfidence.apply(
                floor(0), cameraToWorld(), WALKABLE, samePoint);
        assertFalse(degenerate.planeValid);
        assertEquals(0, degenerate.boostedPoints);
    }

    @Test public void collinearOrMissingDepthCannotCreateAPlane() {
        SemanticPointCloud.Data line = floor(0);
        for (int i = 0; i < line.semanticRgb.length; i++) point(line,i,i,0,2);
        assertFalse(SourceGroundConfidence.apply(line,cameraToWorld(),WALKABLE,new Random(1)).planeValid);
        SemanticPointCloud.Data empty = new SemanticPointCloud.Data(new float[0],new int[0],new float[0]);
        assertFalse(SourceGroundConfidence.apply(empty,cameraToWorld(),WALKABLE).planeValid);
    }

    @Test public void missingOrInvalidPoseLeavesConfidenceUntouched() {
        SemanticPointCloud.Data cloud = floor(0);
        assertFalse(SourceGroundConfidence.apply(cloud,null,WALKABLE).planeValid);
        float[] invalid = cameraToWorld();
        invalid[9] = Float.NaN;
        assertFalse(SourceGroundConfidence.apply(cloud,invalid,WALKABLE).planeValid);
        for (float confidence : cloud.confidence) assertEquals(.3f,confidence,0f);
    }
}
