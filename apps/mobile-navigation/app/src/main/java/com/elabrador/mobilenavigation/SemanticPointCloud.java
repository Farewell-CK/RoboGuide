package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.Intrinsic;

import java.util.Arrays;

/**
 * Platform port of color_pcl_generator.generate_cloud_data_common plus the
 * semantic-max fields. No navigation or obstacle rule is applied here.
 */
final class SemanticPointCloud {
    static final class Data {
        final float[] xyz;
        final int[] semanticRgb;
        final float[] confidence;

        Data(float[] xyz, int[] semanticRgb, float[] confidence) {
            this.xyz = xyz;
            this.semanticRgb = semanticRgb;
            this.confidence = confidence;
        }
    }

    private SemanticPointCloud() {}

    static Data generate(int[] mask, float[] confidence, int width, int height,
                         byte[] depth, int depthWidth, int depthHeight, int depthStride,
                         float depthUnits, Intrinsic intrinsic, int cropX, int cropY,
                         int cropWidth, int cropHeight, int[] classColors) {
        if (width <= 0 || height <= 0 || cropWidth <= 0 || cropHeight <= 0
                || intrinsic == null
                || mask == null || confidence == null || depth == null || classColors == null
                || mask.length < width * height || confidence.length < width * height) {
            return new Data(new float[0], new int[0], new float[0]);
        }

        // The source emits one point for every valid depth pixel. Count them first so the
        // Android port does not retain a full-capacity cloud and a trimmed copy together.
        int capacity = countValidPixels(mask, width, height, depth, depthWidth, depthHeight,
                depthStride, depthUnits, cropX, cropY, cropWidth, cropHeight,
                classColors.length);
        float[] xyz = new float[capacity * 3];
        int[] semanticRgb = new int[capacity];
        float[] conf = new float[capacity];
        float[] pixels = new float[capacity * 2];
        float[] depths = new float[capacity];
        int count = 0;
        for (int y = 0; y < height; y++) {
            int sourceY = cropY + Math.min(cropHeight - 1, y * cropHeight / height);
            if (sourceY < 0 || sourceY >= depthHeight) continue;
            for (int x = 0; x < width; x++) {
                int sourceX = cropX + Math.min(cropWidth - 1, x * cropWidth / width);
                if (sourceX < 0 || sourceX >= depthWidth) continue;
                int rawIndex = sourceY * depthStride + sourceX * 2;
                if (rawIndex < 0 || rawIndex + 1 >= depth.length) continue;
                int rawDepth = (depth[rawIndex] & 0xff) | ((depth[rawIndex + 1] & 0xff) << 8);
                float meters = rawDepth * depthUnits;
                if (!(meters > 0.1f) || !Float.isFinite(meters)) continue;
                int maskIndex = y * width + x;
                int classId = mask[maskIndex];
                if (classId < 0 || classId >= classColors.length) continue;
                pixels[count * 2] = sourceX;
                pixels[count * 2 + 1] = sourceY;
                depths[count] = meters;
                semanticRgb[count] = sourceTreeColor(classColors[classId]);
                conf[count] = confidence[maskIndex];
                count++;
            }
        }
        if (count == 0) {
            return new Data(new float[0], new int[0], new float[0]);
        }
        if (count != capacity) {
            pixels = Arrays.copyOf(pixels, count * 2);
            depths = Arrays.copyOf(depths, count);
            xyz = Arrays.copyOf(xyz, count * 3);
            semanticRgb = Arrays.copyOf(semanticRgb, count);
            conf = Arrays.copyOf(conf, count);
        }
        NativeRealSense.nativeDeprojectPixels(
                intrinsic.getWidth(), intrinsic.getHeight(),
                intrinsic.getPpx(), intrinsic.getPpy(), intrinsic.getFx(), intrinsic.getFy(),
                intrinsic.getModel().value(), intrinsic.getCoeffs(), pixels, depths, xyz);

        int valid = 0;
        for (int index = 0; index < count; index++) {
            float pointX = xyz[index * 3];
            float pointY = xyz[index * 3 + 1];
            float pointZ = xyz[index * 3 + 2];
            if (!Float.isFinite(pointX) || !Float.isFinite(pointY) || !Float.isFinite(pointZ)) {
                continue;
            }
            if (valid != index) {
                xyz[valid * 3] = pointX;
                xyz[valid * 3 + 1] = pointY;
                xyz[valid * 3 + 2] = pointZ;
                semanticRgb[valid] = semanticRgb[index];
                conf[valid] = conf[index];
            }
            valid++;
        }
        if (valid == count) return new Data(xyz, semanticRgb, conf);
        return new Data(Arrays.copyOf(xyz, valid * 3),
                Arrays.copyOf(semanticRgb, valid), Arrays.copyOf(conf, valid));
    }

    private static int countValidPixels(int[] mask, int width, int height, byte[] depth,
                                        int depthWidth, int depthHeight, int depthStride,
                                        float depthUnits, int cropX, int cropY,
                                        int cropWidth, int cropHeight, int classCount) {
        int count = 0;
        for (int y = 0; y < height; y++) {
            int sourceY = cropY + Math.min(cropHeight - 1, y * cropHeight / height);
            if (sourceY < 0 || sourceY >= depthHeight) continue;
            for (int x = 0; x < width; x++) {
                int sourceX = cropX + Math.min(cropWidth - 1, x * cropWidth / width);
                if (sourceX < 0 || sourceX >= depthWidth) continue;
                int rawIndex = sourceY * depthStride + sourceX * 2;
                if (rawIndex < 0 || rawIndex + 1 >= depth.length) continue;
                int rawDepth = (depth[rawIndex] & 0xff) | ((depth[rawIndex + 1] & 0xff) << 8);
                float meters = rawDepth * depthUnits;
                int classId = mask[y * width + x];
                if (meters > 0.1f && Float.isFinite(meters)
                        && classId >= 0 && classId < classCount) {
                    count++;
                }
            }
        }
        return count;
    }

    /** Source PCL semantic_color reaches the OctoMap tree in BGR byte order. */
    static int sourceTreeColor(int rgb) {
        return ((rgb & 0xff) << 16) | (rgb & 0x00ff00) | ((rgb >>> 16) & 0xff);
    }
}
