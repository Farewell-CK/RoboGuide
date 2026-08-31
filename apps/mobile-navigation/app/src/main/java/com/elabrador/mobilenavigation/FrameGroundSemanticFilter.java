package com.elabrador.mobilenavigation;

import java.util.Arrays;

/** Marks only broad, gravity-horizontal, obstacle-free floor cells as walkable. */
final class FrameGroundSemanticFilter {
    static final class Result {
        final float groundHeight;
        final int supportCells;
        final int correctedPoints;

        Result(float groundHeight, int supportCells, int correctedPoints) {
            this.groundHeight = groundHeight;
            this.supportCells = supportCells;
            this.correctedPoints = correctedPoints;
        }
    }

    private static final float MIN_HEIGHT = -2.5f;
    private static final float MAX_HEIGHT = -0.4f;
    private static final float BIN_SIZE = 0.05f;
    private static final float FLOOR_BAND = 0.10f;
    private static final float MAX_TRAVERSABLE_RISE = 0.12f;
    private static final float RADIUS = 7.5f;
    private static final int MIN_SUPPORT_CELLS = 16;
    private static final int MIN_SPAN_CELLS = 4;
    private static final int BIN_COUNT = (int) Math.ceil((MAX_HEIGHT - MIN_HEIGHT) / BIN_SIZE);

    private FrameGroundSemanticFilter() {}

    static Result apply(SemanticPointCloud.Data cloud, float[] cameraToWorld,
                        int walkableTreeColor) {
        if (cloud == null || cameraToWorld == null || cameraToWorld.length != 16
                || cloud.xyz.length / 3 != cloud.semanticRgb.length
                || cloud.confidence.length != cloud.semanticRgb.length) {
            return new Result(Float.NaN, 0, 0);
        }
        int pointCount = cloud.semanticRgb.length;
        int cellCount = MapTransform.WIDTH * MapTransform.HEIGHT;
        boolean[] binCells = new boolean[BIN_COUNT * cellCount];
        float[] maxCellHeight = new float[cellCount];
        Arrays.fill(maxCellHeight, Float.NEGATIVE_INFINITY);
        int[] pointCells = new int[pointCount];
        float[] pointHeights = new float[pointCount];
        Arrays.fill(pointCells, -1);
        float cameraX = cameraToWorld[3], cameraY = cameraToWorld[7], cameraZ = cameraToWorld[11];
        float origin = -RADIUS - MapTransform.RESOLUTION * 2f;

        for (int point = 0; point < pointCount; point++) {
            int offset = point * 3;
            float sensorX = cloud.xyz[offset];
            float sensorY = cloud.xyz[offset + 1];
            float sensorZ = cloud.xyz[offset + 2];
            float worldX = cameraToWorld[0] * sensorX + cameraToWorld[1] * sensorY
                    + cameraToWorld[2] * sensorZ + cameraX;
            float worldY = cameraToWorld[4] * sensorX + cameraToWorld[5] * sensorY
                    + cameraToWorld[6] * sensorZ + cameraY;
            float worldZ = cameraToWorld[8] * sensorX + cameraToWorld[9] * sensorY
                    + cameraToWorld[10] * sensorZ + cameraZ;
            float relativeX = worldX - cameraX;
            float relativeY = worldY - cameraY;
            float relativeHeight = worldZ - cameraZ;
            if (Math.abs(relativeX) > RADIUS || Math.abs(relativeY) > RADIUS
                    || relativeHeight < MIN_HEIGHT || relativeHeight > 0f) continue;
            int x = (int) ((relativeX - origin) / MapTransform.RESOLUTION);
            int y = (int) ((relativeY - origin) / MapTransform.RESOLUTION);
            if (x < 0 || x >= MapTransform.WIDTH || y < 0 || y >= MapTransform.HEIGHT) continue;
            int cell = y * MapTransform.WIDTH + x;
            pointCells[point] = cell;
            pointHeights[point] = relativeHeight;
            maxCellHeight[cell] = Math.max(maxCellHeight[cell], relativeHeight);
            int bin = binForHeight(relativeHeight);
            if (bin >= 0) binCells[bin * cellCount + cell] = true;
        }

        int[] support = new int[BIN_COUNT];
        int strongest = 0;
        boolean[] broad = new boolean[BIN_COUNT];
        for (int bin = 0; bin < BIN_COUNT; bin++) {
            int minX = MapTransform.WIDTH, maxX = -1;
            int minY = MapTransform.HEIGHT, maxY = -1;
            for (int cell = 0; cell < cellCount; cell++) {
                if (!hasBinEvidence(binCells, cellCount, cell, bin, 1)) continue;
                support[bin]++;
                int x = cell % MapTransform.WIDTH;
                int y = cell / MapTransform.WIDTH;
                minX = Math.min(minX, x); maxX = Math.max(maxX, x);
                minY = Math.min(minY, y); maxY = Math.max(maxY, y);
            }
            broad[bin] = maxX >= 0 && maxX - minX + 1 >= MIN_SPAN_CELLS
                    && maxY - minY + 1 >= MIN_SPAN_CELLS;
            if (broad[bin]) strongest = Math.max(strongest, support[bin]);
        }
        if (strongest < MIN_SUPPORT_CELLS) return new Result(Float.NaN, 0, 0);
        int required = Math.max(MIN_SUPPORT_CELLS, (int) Math.ceil(strongest * 0.40f));
        int groundBin = -1;
        for (int bin = 0; bin < BIN_COUNT; bin++) {
            if (broad[bin] && support[bin] >= required) {
                groundBin = bin;
                break;
            }
        }
        if (groundBin < 0) return new Result(Float.NaN, 0, 0);

        float groundHeight = heightForBin(groundBin);
        int corrected = 0;
        for (int point = 0; point < pointCount; point++) {
            int cell = pointCells[point];
            if (cell < 0 || Math.abs(pointHeights[point] - groundHeight) > FLOOR_BAND
                    || maxCellHeight[cell] - groundHeight > MAX_TRAVERSABLE_RISE) continue;
            if (cloud.semanticRgb[point] != walkableTreeColor) corrected++;
            cloud.semanticRgb[point] = walkableTreeColor;
            cloud.confidence[point] = Math.max(cloud.confidence[point], 1f);
        }
        return new Result(groundHeight, support[groundBin], corrected);
    }

    private static boolean hasBinEvidence(boolean[] cells, int cellCount, int cell,
                                          int centerBin, int radius) {
        int first = Math.max(0, centerBin - radius);
        int last = Math.min(BIN_COUNT - 1, centerBin + radius);
        for (int bin = first; bin <= last; bin++) {
            if (cells[bin * cellCount + cell]) return true;
        }
        return false;
    }

    private static int binForHeight(float height) {
        if (height < MIN_HEIGHT || height > MAX_HEIGHT) return -1;
        return Math.min(BIN_COUNT - 1, Math.max(0, (int) ((height - MIN_HEIGHT) / BIN_SIZE)));
    }

    private static float heightForBin(int bin) {
        return MIN_HEIGHT + (bin + 0.5f) * BIN_SIZE;
    }
}
