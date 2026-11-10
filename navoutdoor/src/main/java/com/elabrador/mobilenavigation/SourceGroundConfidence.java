package com.elabrador.mobilenavigation;

import java.util.Random;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Port of upstream Detector.get_road_plane / predict's confidence-only boost.
 *
 * The upstream detector fits full-resolution depth; this port fits the valid
 * points already sampled for the Android semantic cloud. It never changes a
 * semantic color, assigns a walkable class, or clears an obstacle cost.
 */
final class SourceGroundConfidence {
    private static final int TRIALS = 20;
    private static final float MIN_DEPTH = 0.1f;
    private static final float FIT_MAX_DEPTH = 10f;
    private static final float INLIER_DISTANCE = 0.1f;
    private static final double MIN_NORMAL_LENGTH = 1e-4;
    private static final double MIN_WORLD_VERTICAL_COSINE = 0.71;

    static final class Result {
        final int boostedPoints;
        final int inliers;
        final boolean planeValid;
        final int candidatePoints;

        Result(int boostedPoints, int inliers, boolean planeValid, int candidatePoints) {
            this.boostedPoints = boostedPoints;
            this.inliers = inliers;
            this.planeValid = planeValid;
            this.candidatePoints = candidatePoints;
        }
    }

    private SourceGroundConfidence() {}

    static Result apply(SemanticPointCloud.Data cloud, float[] cameraToWorld,
                        int[] walkableTreeColors) {
        return apply(cloud, cameraToWorld, walkableTreeColors, ThreadLocalRandom.current());
    }

    /** Injectable random source keeps tests independent of a lucky RANSAC draw. */
    static Result apply(SemanticPointCloud.Data cloud, float[] cameraToWorld,
                        int[] walkableTreeColors, Random random) {
        if (cloud == null || cloud.xyz == null || cloud.semanticRgb == null
                || cloud.confidence == null || cameraToWorld == null
                || cameraToWorld.length != 16 || walkableTreeColors == null || random == null
                || cloud.xyz.length % 3 != 0
                || cloud.xyz.length / 3 != cloud.semanticRgb.length
                || cloud.confidence.length != cloud.semanticRgb.length) {
            return new Result(0, 0, false, 0);
        }
        for (float value : cameraToWorld) {
            if (!Float.isFinite(value)) return new Result(0, 0, false, 0);
        }

        int[] candidates = new int[cloud.semanticRgb.length];
        int candidateCount = 0;
        for (int i = 0; i < cloud.semanticRgb.length; i++) {
            int p = i * 3;
            if (validDepthPoint(cloud.xyz, p) && cloud.xyz[p + 2] < FIT_MAX_DEPTH) {
                candidates[candidateCount++] = p;
            }
        }
        if (candidateCount < 3) return new Result(0, 0, false, candidateCount);

        int bestInliers = 0;
        double bestX = 0, bestY = 0, bestZ = 0, bestOffset = 0;
        for (int trial = 0; trial < TRIALS; trial++) {
            // np.random.choice(len(points), 3) samples with replacement by default.
            int a = candidates[random.nextInt(candidateCount)];
            int b = candidates[random.nextInt(candidateCount)];
            int c = candidates[random.nextInt(candidateCount)];
            double ux = cloud.xyz[b] - cloud.xyz[a];
            double uy = cloud.xyz[b + 1] - cloud.xyz[a + 1];
            double uz = cloud.xyz[b + 2] - cloud.xyz[a + 2];
            double vx = cloud.xyz[c] - cloud.xyz[a];
            double vy = cloud.xyz[c + 1] - cloud.xyz[a + 1];
            double vz = cloud.xyz[c + 2] - cloud.xyz[a + 2];
            double nx = uy * vz - uz * vy;
            double ny = uz * vx - ux * vz;
            double nz = ux * vy - uy * vx;
            double length = Math.sqrt(nx * nx + ny * ny + nz * nz);
            if (length < MIN_NORMAL_LENGTH) continue;
            nx /= length;
            ny /= length;
            nz /= length;
            double offset = -(nx * cloud.xyz[a] + ny * cloud.xyz[a + 1]
                    + nz * cloud.xyz[a + 2]);
            int inliers = 0;
            for (int i = 0; i < candidateCount; i++) {
                int p = candidates[i];
                if (Math.abs(nx * cloud.xyz[p] + ny * cloud.xyz[p + 1]
                        + nz * cloud.xyz[p + 2] + offset) < INLIER_DISTANCE) inliers++;
            }
            // np.argmax retains the first winner when inlier counts tie.
            if (inliers > bestInliers) {
                bestInliers = inliers;
                bestX = nx;
                bestY = ny;
                bestZ = nz;
                bestOffset = offset;
            }
        }
        if (bestInliers == 0) return new Result(0, 0, false, candidateCount);

        // Upstream transforms normal and camera origin as points, then subtracts
        // them. Applying only the rotation avoids introducing translation error.
        double worldX = cameraToWorld[0] * bestX + cameraToWorld[1] * bestY
                + cameraToWorld[2] * bestZ;
        double worldY = cameraToWorld[4] * bestX + cameraToWorld[5] * bestY
                + cameraToWorld[6] * bestZ;
        double worldZ = cameraToWorld[8] * bestX + cameraToWorld[9] * bestY
                + cameraToWorld[10] * bestZ;
        double worldLength = Math.sqrt(worldX * worldX + worldY * worldY + worldZ * worldZ);
        if (!(worldLength > 0) || Math.abs(worldZ / worldLength) < MIN_WORLD_VERTICAL_COSINE) {
            return new Result(0, bestInliers, false, candidateCount);
        }

        int boosted = 0;
        for (int i = 0; i < cloud.semanticRgb.length; i++) {
            int p = i * 3;
            // The source uses depth < 10 only for fitting. Its resulting mask
            // covers every valid depth pixel, including those farther than 10 m.
            if (!validDepthPoint(cloud.xyz, p)
                    || Math.abs(bestX * cloud.xyz[p] + bestY * cloud.xyz[p + 1]
                    + bestZ * cloud.xyz[p + 2] + bestOffset) >= INLIER_DISTANCE
                    || !contains(walkableTreeColors, cloud.semanticRgb[i])) continue;
            cloud.confidence[i] *= 10f;
            boosted++;
        }
        return new Result(boosted, bestInliers, true, candidateCount);
    }

    private static boolean validDepthPoint(float[] xyz, int offset) {
        return Float.isFinite(xyz[offset]) && Float.isFinite(xyz[offset + 1])
                && Float.isFinite(xyz[offset + 2]) && xyz[offset + 2] > MIN_DEPTH;
    }

    private static boolean contains(int[] colors, int color) {
        for (int candidate : colors) if (candidate == color) return true;
        return false;
    }
}
