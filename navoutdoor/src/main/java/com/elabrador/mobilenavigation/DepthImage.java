package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.DepthFrame;

/** Owned Z16 snapshot; safe after the originating RealSense frame is released. */
final class DepthImage {
    final byte[] data;
    final int width, height, stride;
    final float units;

    DepthImage(byte[] data, int width, int height, int stride, float units) {
        this.data = data;
        this.width = width;
        this.height = height;
        this.stride = stride;
        this.units = units;
    }

    static DepthImage copy(DepthFrame frame) {
        byte[] data = new byte[frame.getDataSize()];
        frame.getData(data);
        return new DepthImage(data, frame.getWidth(), frame.getHeight(), frame.getStride(),
                frame.getUnits());
    }

    int getWidth() { return width; }
    int getHeight() { return height; }
    int getStride() { return stride; }
    int getDataSize() { return data.length; }
    float getUnits() { return units; }
    void getData(byte[] destination) { System.arraycopy(data, 0, destination, 0, data.length); }
    float getDistance(int x, int y) {
        int i = y * stride + x * 2;
        return ((data[i] & 255) | ((data[i + 1] & 255) << 8)) * units;
    }
}
