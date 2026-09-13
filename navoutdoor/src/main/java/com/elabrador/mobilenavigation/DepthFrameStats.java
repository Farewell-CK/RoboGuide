package com.elabrador.mobilenavigation;

final class DepthFrameStats {
    private DepthFrameStats() {}

    static int countValidSamples(byte[] depth, int width, int height,
                                 int stride, int downsample) {
        if (depth == null || width <= 0 || height <= 0 || stride <= 0 || downsample <= 0) {
            return 0;
        }
        int valid = 0;
        for (int y = 0; y < height; y += downsample) {
            int row = y * stride;
            for (int x = 0; x < width; x += downsample) {
                int index = row + x * 2;
                if (index < 0 || index + 1 >= depth.length) continue;
                if (depth[index] != 0 || depth[index + 1] != 0) valid++;
            }
        }
        return valid;
    }
}
