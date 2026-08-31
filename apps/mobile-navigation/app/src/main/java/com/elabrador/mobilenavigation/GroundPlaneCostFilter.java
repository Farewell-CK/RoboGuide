package com.elabrador.mobilenavigation;

import java.util.Arrays;

/**
 * Removes a semantic false positive only when the same cell is supported by a
 * broad, gravity-horizontal ground plane and contains nothing above that plane.
 */
final class GroundPlaneCostFilter {
    static final float MIN_HEIGHT = -2.5f;
    static final float MAX_HEIGHT = -0.4f;
    static final float BIN_SIZE = 0.05f;
    static final float FLOOR_BAND = 0.10f;
    static final float MAX_TRAVERSABLE_RISE = 0.12f;
    static final int MIN_SUPPORT_CELLS = 16;
    static final int MIN_SPAN_CELLS = 4;

    static final class Result {
        final float groundHeight;
        final int clearedCells;
        final int groundSupportCells;
        final int positiveCostCells;
        final int groundCandidateCells;

        Result(float groundHeight, int clearedCells, int groundSupportCells,
               int positiveCostCells, int groundCandidateCells) {
            this.groundHeight = groundHeight;
            this.clearedCells = clearedCells;
            this.groundSupportCells = groundSupportCells;
            this.positiveCostCells = positiveCostCells;
            this.groundCandidateCells = groundCandidateCells;
        }

        boolean hasGround() {
            return Float.isFinite(groundHeight);
        }
    }

    private final int width;
    private final int height;
    private final int cellCount;
    private final int binCount;
    private final boolean[] heightBinCells;
    private final float[] maxCellHeight;

    GroundPlaneCostFilter(int width, int height) {
        this.width = width;
        this.height = height;
        cellCount = width * height;
        binCount = (int) Math.ceil((MAX_HEIGHT - MIN_HEIGHT) / BIN_SIZE);
        heightBinCells = new boolean[binCount * cellCount];
        maxCellHeight = new float[cellCount];
        Arrays.fill(maxCellHeight, Float.NEGATIVE_INFINITY);
    }

    void observe(int cell, float relativeHeight) {
        if (cell < 0 || cell >= cellCount || !Float.isFinite(relativeHeight)) return;
        if (relativeHeight > maxCellHeight[cell]) maxCellHeight[cell] = relativeHeight;
        int bin = binForHeight(relativeHeight);
        if (bin >= 0) heightBinCells[bin * cellCount + cell] = true;
    }

    Result apply(int[] cost) {
        if (cost == null || cost.length != cellCount) {
            return new Result(Float.NaN, 0, 0, 0, 0);
        }
        int bestBin = findGroundBin();
        if (bestBin < 0) return new Result(Float.NaN, 0, 0, countPositive(cost), 0);

        float groundHeight = heightForBin(bestBin);
        int cleared = 0;
        int candidates = 0;
        for (int cell = 0; cell < cellCount; cell++) {
            if (cost[cell] <= 0 || !hasGroundEvidence(cell, groundHeight)) continue;
            candidates++;
            if (maxCellHeight[cell] - groundHeight <= MAX_TRAVERSABLE_RISE) {
                cost[cell] = 0;
                cleared++;
            }
        }
        return new Result(groundHeight, cleared, supportForBin(bestBin),
                countPositive(cost) + cleared, candidates);
    }

    private int findGroundBin() {
        int[] support = new int[binCount];
        int[] spanX = new int[binCount];
        int[] spanY = new int[binCount];
        int strongest = 0;
        for (int bin = 0; bin < binCount; bin++) {
            int minX = width, maxX = -1, minY = height, maxY = -1;
            for (int cell = 0; cell < cellCount; cell++) {
                if (!hasBinEvidence(cell, bin, 1)) continue;
                support[bin]++;
                int x = cell % width;
                int y = cell / width;
                if (x < minX) minX = x;
                if (x > maxX) maxX = x;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
            }
            spanX[bin] = maxX < 0 ? 0 : maxX - minX + 1;
            spanY[bin] = maxY < 0 ? 0 : maxY - minY + 1;
            if (spanX[bin] >= MIN_SPAN_CELLS && spanY[bin] >= MIN_SPAN_CELLS) {
                strongest = Math.max(strongest, support[bin]);
            }
        }
        if (strongest < MIN_SUPPORT_CELLS) return -1;

        // A floor is the lowest broad plane. Requiring substantial support keeps a
        // sparse wall base or a few low outliers from becoming a traversable plane.
        int required = Math.max(MIN_SUPPORT_CELLS, (int) Math.ceil(strongest * 0.40f));
        for (int bin = 0; bin < binCount; bin++) {
            if (support[bin] >= required
                    && spanX[bin] >= MIN_SPAN_CELLS
                    && spanY[bin] >= MIN_SPAN_CELLS) {
                return bin;
            }
        }
        return -1;
    }

    private boolean hasGroundEvidence(int cell, float groundHeight) {
        int center = binForHeight(groundHeight);
        int radius = Math.max(1, (int) Math.ceil(FLOOR_BAND / BIN_SIZE));
        return center >= 0 && hasBinEvidence(cell, center, radius);
    }

    private boolean hasBinEvidence(int cell, int centerBin, int radius) {
        int first = Math.max(0, centerBin - radius);
        int last = Math.min(binCount - 1, centerBin + radius);
        for (int bin = first; bin <= last; bin++) {
            if (heightBinCells[bin * cellCount + cell]) return true;
        }
        return false;
    }

    private int supportForBin(int bin) {
        int support = 0;
        for (int cell = 0; cell < cellCount; cell++) {
            if (hasBinEvidence(cell, bin, 1)) support++;
        }
        return support;
    }

    private int countPositive(int[] cost) {
        int count = 0;
        for (int value : cost) if (value > 0) count++;
        return count;
    }

    private int binForHeight(float relativeHeight) {
        if (relativeHeight < MIN_HEIGHT || relativeHeight > MAX_HEIGHT) return -1;
        int bin = (int) ((relativeHeight - MIN_HEIGHT) / BIN_SIZE);
        return Math.min(binCount - 1, Math.max(0, bin));
    }

    private float heightForBin(int bin) {
        return MIN_HEIGHT + (bin + 0.5f) * BIN_SIZE;
    }
}
